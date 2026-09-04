package connect

import (
	"context"
	"crypto/rand"
	"encoding/binary"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"net"
	"strconv"
	"strings"
	"sync"
	"time"
)

// Ordered public broker list. The PC holds a rendezvous on every broker; the
// phone tries them in this order and meets the PC on the first both can reach.
var PublicRDVEndpoints = []struct {
	Host string
	Port int
}{
	{"broker.emqx.io", 1883},
	{"broker.hivemq.com", 1883},
	{"test.mosquitto.org", 1883},
}

const (
	KeepaliveS  = 30
	PingEveryS  = 12 * time.Second
	ClientIDLen = 16

	mqttConnect    = 0x10
	mqttConnack    = 0x20
	mqttPublish    = 0x30
	mqttPuback     = 0x40
	mqttSubscribe  = 0x80
	mqttSuback     = 0x90
	mqttPingreq    = 0xC0
	mqttPingresp   = 0xD0
	mqttDisconnect = 0xE0
)

var (
	// ErrMQTT is a codec / session failure (fail closed).
	ErrMQTT = errors.New("mqtt error")

	pingReqPacket    = []byte{mqttPingreq, 0x00}
	pingRespPacket   = []byte{mqttPingresp, 0x00}
	disconnectPacket = []byte{mqttDisconnect, 0x00}
)

// EncodeVarint encodes an MQTT remaining-length varint (1..4 bytes).
func EncodeVarint(value int) ([]byte, error) {
	if value < 0 || value > 268435455 {
		return nil, fmt.Errorf("%w: varint out of range: %d", ErrMQTT, value)
	}
	out := make([]byte, 0, 4)
	for {
		b := byte(value % 128)
		value /= 128
		if value > 0 {
			b |= 0x80
		}
		out = append(out, b)
		if value == 0 {
			return out, nil
		}
	}
}

// DecodeVarint returns (value, bytesConsumed) from data[offset:].
func DecodeVarint(data []byte, offset int) (int, int, error) {
	value := 0
	multiplier := 1
	i := offset
	for {
		if i >= len(data) {
			return 0, 0, fmt.Errorf("%w: truncated varint", ErrMQTT)
		}
		b := data[i]
		value += int(b&0x7F) * multiplier
		if b&0x80 == 0 {
			return value, i - offset + 1, nil
		}
		if multiplier > 128*128*128 {
			return 0, 0, fmt.Errorf("%w: varint too long", ErrMQTT)
		}
		multiplier *= 128
		i++
	}
}

func utf8Field(text string) ([]byte, error) {
	raw := []byte(text)
	if len(raw) > 65535 {
		return nil, fmt.Errorf("%w: MQTT string too long", ErrMQTT)
	}
	out := make([]byte, 2+len(raw))
	binary.BigEndian.PutUint16(out[:2], uint16(len(raw)))
	copy(out[2:], raw)
	return out, nil
}

// BuildConnect builds an MQTT 3.1.1 CONNECT packet (clean session).
func BuildConnect(clientID string, keepalive int) ([]byte, error) {
	if keepalive < 0 || keepalive > 65535 {
		return nil, fmt.Errorf("%w: keepalive out of range", ErrMQTT)
	}
	idField, err := utf8Field(clientID)
	if err != nil {
		return nil, err
	}
	body := make([]byte, 0, 10+len(idField))
	body = append(body, 0x00, 0x04, 'M', 'Q', 'T', 'T') // protocol name
	body = append(body, 0x04)                           // level 3.1.1
	body = append(body, 0x02)                           // clean session
	var kb [2]byte
	binary.BigEndian.PutUint16(kb[:], uint16(keepalive))
	body = append(body, kb[:]...)
	body = append(body, idField...)
	return wrapMQTT(mqttConnect, body)
}

func wrapMQTT(typeFlags byte, body []byte) ([]byte, error) {
	vl, err := EncodeVarint(len(body))
	if err != nil {
		return nil, err
	}
	out := make([]byte, 1+len(vl)+len(body))
	out[0] = typeFlags
	copy(out[1:], vl)
	copy(out[1+len(vl):], body)
	return out, nil
}

// ParseConnack returns the MQTT return code (0 = accepted).
func ParseConnack(data []byte) (int, error) {
	if len(data) < 4 || data[0] != mqttConnack {
		return 0, fmt.Errorf("%w: not a CONNACK", ErrMQTT)
	}
	remaining, consumed, err := DecodeVarint(data, 1)
	if err != nil {
		return 0, err
	}
	if remaining < 2 || 1+consumed+2 > len(data) {
		return 0, fmt.Errorf("%w: truncated CONNACK", ErrMQTT)
	}
	return int(data[2+consumed]), nil
}

// BuildSubscribe builds an MQTT SUBSCRIBE (QoS bit set on fixed header).
func BuildSubscribe(packetID int, topics []string, qos int) ([]byte, error) {
	body := make([]byte, 2)
	binary.BigEndian.PutUint16(body, uint16(packetID&0xFFFF))
	for _, topic := range topics {
		field, err := utf8Field(topic)
		if err != nil {
			return nil, err
		}
		body = append(body, field...)
		body = append(body, byte(qos&0x03))
	}
	return wrapMQTT(mqttSubscribe|0x02, body)
}

// ParseSuback returns (packetID, returnCodes).
func ParseSuback(data []byte) (int, []int, error) {
	if len(data) < 5 || data[0] != mqttSuback {
		return 0, nil, fmt.Errorf("%w: not a SUBACK", ErrMQTT)
	}
	remaining, consumed, err := DecodeVarint(data, 1)
	if err != nil {
		return 0, nil, err
	}
	body := data[1+consumed:]
	if len(body) < 3 || len(body) != remaining {
		return 0, nil, fmt.Errorf("%w: truncated SUBACK", ErrMQTT)
	}
	packetID := int(binary.BigEndian.Uint16(body[:2]))
	codes := make([]int, len(body)-2)
	for i, c := range body[2:] {
		codes[i] = int(c)
	}
	return packetID, codes, nil
}

// BuildPublish builds an MQTT PUBLISH. qos 1 includes a packet id.
func BuildPublish(packetID int, topic string, payload []byte, qos int) ([]byte, error) {
	if len(payload) > MaxRecord {
		return nil, fmt.Errorf("%w: publish payload too large", ErrMQTT)
	}
	field, err := utf8Field(topic)
	if err != nil {
		return nil, err
	}
	body := append([]byte{}, field...)
	if qos > 0 {
		var id [2]byte
		binary.BigEndian.PutUint16(id[:], uint16(packetID&0xFFFF))
		body = append(body, id[:]...)
	}
	body = append(body, payload...)
	flags := byte(0x00)
	if qos == 1 {
		flags = 0x02
	}
	return wrapMQTT(mqttPublish|flags, body)
}

// MqttMessage is one decoded PUBLISH.
type MqttMessage struct {
	Topic    string
	Payload  []byte
	QoS      int
	PacketID int
}

// ParsePublish decodes one PUBLISH packet.
func ParsePublish(data []byte) (MqttMessage, error) {
	var zero MqttMessage
	if len(data) < 2 || data[0]&0xF0 != mqttPublish {
		return zero, fmt.Errorf("%w: not a PUBLISH", ErrMQTT)
	}
	remaining, consumed, err := DecodeVarint(data, 1)
	if err != nil {
		return zero, err
	}
	body := data[1+consumed:]
	if len(body) != remaining {
		return zero, fmt.Errorf("%w: truncated PUBLISH", ErrMQTT)
	}
	qos := int((data[0] >> 1) & 0x03)
	if qos == 3 {
		return zero, fmt.Errorf("%w: invalid PUBLISH QoS", ErrMQTT)
	}
	if len(body) < 2 {
		return zero, fmt.Errorf("%w: truncated PUBLISH topic", ErrMQTT)
	}
	tlen := int(binary.BigEndian.Uint16(body[:2]))
	if 2+tlen > len(body) {
		return zero, fmt.Errorf("%w: truncated PUBLISH topic", ErrMQTT)
	}
	topic := string(body[2 : 2+tlen])
	offset := 2 + tlen
	packetID := 0
	if qos > 0 {
		if offset+2 > len(body) {
			return zero, fmt.Errorf("%w: truncated PUBLISH id", ErrMQTT)
		}
		packetID = int(binary.BigEndian.Uint16(body[offset : offset+2]))
		offset += 2
	}
	payload := append([]byte{}, body[offset:]...)
	return MqttMessage{Topic: topic, Payload: payload, QoS: qos, PacketID: packetID}, nil
}

// BuildPuback builds an MQTT PUBACK.
func BuildPuback(packetID int) []byte {
	out := []byte{mqttPuback, 0x02, 0, 0}
	binary.BigEndian.PutUint16(out[2:], uint16(packetID&0xFFFF))
	return out
}

// PingReqPacket / PingRespPacket / DisconnectPacket are fixed MQTT control packets.
func PingReqPacket() []byte    { return append([]byte{}, pingReqPacket...) }
func PingRespPacket() []byte   { return append([]byte{}, pingRespPacket...) }
func DisconnectPacket() []byte { return append([]byte{}, disconnectPacket...) }

// RDVTopic is one direction's topic: remedy/<sid-hex>/<side>.
func RDVTopic(sid []byte, side string) (string, error) {
	if len(sid) != SessionIDLen {
		return "", fmt.Errorf("session id must be 16 bytes")
	}
	if side != "pc" && side != "phone" {
		return "", fmt.Errorf("rdv side must be 'pc' or 'phone'")
	}
	return "remedy/" + hex.EncodeToString(sid) + "/" + side, nil
}

// RDVQRValue is the semicolon-separated host:port list for the pairing QR rdv= line.
func RDVQRValue() string {
	parts := make([]string, 0, len(PublicRDVEndpoints))
	for _, ep := range PublicRDVEndpoints {
		parts = append(parts, fmt.Sprintf("%s:%d", ep.Host, ep.Port))
	}
	return strings.Join(parts, ";")
}

// ParseRDVEndpoints parses the QR rdv= value (fail closed if empty after filtering).
func ParseRDVEndpoints(value string) ([]struct {
	Host string
	Port int
}, error) {
	var out []struct {
		Host string
		Port int
	}
	for _, chunk := range strings.Split(value, ";") {
		chunk = strings.TrimSpace(chunk)
		if chunk == "" {
			continue
		}
		host, port, err := ParseRelayEndpoint(chunk)
		if err != nil {
			continue
		}
		out = append(out, struct {
			Host string
			Port int
		}{Host: host, Port: port})
	}
	if len(out) == 0 {
		return nil, fmt.Errorf("rdv list is empty")
	}
	return out, nil
}

// ---------------------------------------------------------------------------
// MQTT session (QoS1) + rendezvous bridge.
// ---------------------------------------------------------------------------

// MqttSession is a minimal MQTT 3.1.1 client over a TCP conn. QoS1 only.
type MqttSession struct {
	Host      string
	Port      int
	ClientID  string
	Keepalive int
	Timeout   time.Duration

	OnMessage func(topic string, payload []byte)

	mu      sync.Mutex
	conn    net.Conn
	pktID   int
	pubWait chan struct{}
	closed  bool
	readMu  sync.Mutex // serializes _readPacket during subscribe handshake
}

// NewMqttSession builds a session with a random client id when empty.
func NewMqttSession(host string, port int, timeout time.Duration) (*MqttSession, error) {
	id := make([]byte, ClientIDLen)
	if _, err := rand.Read(id); err != nil {
		return nil, err
	}
	if timeout <= 0 {
		timeout = 8 * time.Second
	}
	return &MqttSession{
		Host:      host,
		Port:      port,
		ClientID:  hex.EncodeToString(id),
		Keepalive: KeepaliveS,
		Timeout:   timeout,
		pktID:     1,
	}, nil
}

// Connect dials the broker and completes CONNECT/CONNACK.
func (s *MqttSession) Connect(ctx context.Context) error {
	addr := net.JoinHostPort(s.Host, strconv.Itoa(s.Port))
	d := net.Dialer{Timeout: s.Timeout}
	conn, err := d.DialContext(ctx, "tcp", addr)
	if err != nil {
		return err
	}
	s.mu.Lock()
	s.conn = conn
	s.closed = false
	s.mu.Unlock()

	pkt, err := BuildConnect(s.ClientID, s.Keepalive)
	if err != nil {
		s.Close()
		return err
	}
	if err := s.writeAll(pkt); err != nil {
		s.Close()
		return err
	}
	_ = conn.SetReadDeadline(time.Now().Add(s.Timeout))
	resp := make([]byte, 4)
	if _, err := io.ReadFull(conn, resp); err != nil {
		s.Close()
		return err
	}
	_ = conn.SetReadDeadline(time.Time{})
	code, err := ParseConnack(resp)
	if err != nil {
		s.Close()
		return err
	}
	if code != 0 {
		s.Close()
		return fmt.Errorf("%w: MQTT broker refused (%d)", ErrMQTT, code)
	}
	return nil
}

// Subscribe issues SUBSCRIBE and waits for SUBACK (draining intervening PUBLISH).
func (s *MqttSession) Subscribe(ctx context.Context, topics []string, qos int) error {
	packetID := s.nextID()
	pkt, err := BuildSubscribe(packetID, topics, qos)
	if err != nil {
		return err
	}
	if err := s.writeAll(pkt); err != nil {
		return err
	}
	for {
		select {
		case <-ctx.Done():
			return ctx.Err()
		default:
		}
		msg, err := s.readPacket(ctx)
		if err != nil {
			return err
		}
		if msg == nil {
			return fmt.Errorf("%w: MQTT stream closed during SUBSCRIBE", ErrMQTT)
		}
		if msg[0] == mqttSuback {
			_, codes, err := ParseSuback(msg)
			if err != nil {
				return err
			}
			for _, c := range codes {
				if c == 0x80 {
					return fmt.Errorf("%w: MQTT broker refused subscription", ErrMQTT)
				}
			}
			return nil
		}
		if msg[0]&0xF0 == mqttPublish {
			if err := s.handleInboundPublish(msg); err != nil {
				return err
			}
		}
	}
}

// Publish sends a PUBLISH and, for QoS>0, waits for PUBACK.
func (s *MqttSession) Publish(ctx context.Context, topic string, payload []byte, qos int) error {
	packetID := 0
	var waiter chan struct{}
	if qos > 0 {
		packetID = s.nextID()
		waiter = make(chan struct{}, 1)
		s.mu.Lock()
		s.pubWait = waiter
		s.mu.Unlock()
	}
	pkt, err := BuildPublish(packetID, topic, payload, qos)
	if err != nil {
		return err
	}
	if err := s.writeAll(pkt); err != nil {
		return err
	}
	if waiter == nil {
		return nil
	}
	timer := time.NewTimer(20 * time.Second)
	defer timer.Stop()
	select {
	case <-waiter:
		return nil
	case <-timer.C:
		return fmt.Errorf("%w: PUBACK timeout", ErrMQTT)
	case <-ctx.Done():
		return ctx.Err()
	}
}

// ReaderLoop consumes inbound packets until closed.
func (s *MqttSession) ReaderLoop(ctx context.Context) {
	for {
		select {
		case <-ctx.Done():
			return
		default:
		}
		s.mu.Lock()
		closed := s.closed
		s.mu.Unlock()
		if closed {
			return
		}
		msg, err := s.readPacket(ctx)
		if err != nil || msg == nil {
			return
		}
		if msg[0] == mqttPuback {
			s.mu.Lock()
			w := s.pubWait
			s.pubWait = nil
			s.mu.Unlock()
			if w != nil {
				select {
				case w <- struct{}{}:
				default:
				}
			}
		} else if msg[0]&0xF0 == mqttPublish {
			_ = s.handleInboundPublish(msg)
		}
	}
}

// PingLoop sends PINGREQ every PingEveryS while open.
func (s *MqttSession) PingLoop(ctx context.Context) {
	t := time.NewTicker(PingEveryS)
	defer t.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-t.C:
			s.mu.Lock()
			closed := s.closed
			conn := s.conn
			s.mu.Unlock()
			if closed || conn == nil {
				return
			}
			_ = s.writeAll(pingReqPacket)
		}
	}
}

// Close sends DISCONNECT and closes the conn.
func (s *MqttSession) Close() {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.closed = true
	if s.conn != nil {
		_, _ = s.conn.Write(disconnectPacket)
		_ = s.conn.Close()
		s.conn = nil
	}
	if s.pubWait != nil {
		select {
		case s.pubWait <- struct{}{}:
		default:
		}
		s.pubWait = nil
	}
}

func (s *MqttSession) handleInboundPublish(msg []byte) error {
	parsed, err := ParsePublish(msg)
	if err != nil {
		return err
	}
	if parsed.QoS > 0 {
		if err := s.writeAll(BuildPuback(parsed.PacketID)); err != nil {
			return err
		}
	}
	handler := s.OnMessage
	if handler != nil && parsed.Topic != "" {
		handler(parsed.Topic, parsed.Payload)
	}
	return nil
}

func (s *MqttSession) writeAll(pkt []byte) error {
	s.mu.Lock()
	conn := s.conn
	closed := s.closed
	s.mu.Unlock()
	if closed || conn == nil {
		return fmt.Errorf("%w: MQTT session not connected", ErrMQTT)
	}
	_, err := conn.Write(pkt)
	return err
}

func (s *MqttSession) nextID() int {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.pktID = (s.pktID % 0xFFFF) + 1
	return s.pktID
}

func (s *MqttSession) readPacket(ctx context.Context) ([]byte, error) {
	s.readMu.Lock()
	defer s.readMu.Unlock()

	s.mu.Lock()
	conn := s.conn
	s.mu.Unlock()
	if conn == nil {
		return nil, nil
	}

	deadline := time.Now().Add(s.Timeout)
	if dl, ok := ctx.Deadline(); ok && dl.Before(deadline) {
		deadline = dl
	}
	_ = conn.SetReadDeadline(deadline)

	first := make([]byte, 1)
	if _, err := io.ReadFull(conn, first); err != nil {
		if errors.Is(err, io.EOF) || errors.Is(err, io.ErrUnexpectedEOF) {
			return nil, nil
		}
		return nil, err
	}
	typeFlags := first[0]
	varint := make([]byte, 0, 4)
	multiplier := 1
	value := 0
	for {
		b := make([]byte, 1)
		if _, err := io.ReadFull(conn, b); err != nil {
			if errors.Is(err, io.EOF) || errors.Is(err, io.ErrUnexpectedEOF) {
				return nil, nil
			}
			return nil, err
		}
		varint = append(varint, b[0])
		value += int(b[0]&0x7F) * multiplier
		if b[0]&0x80 == 0 {
			break
		}
		if multiplier > 128*128*128 {
			return nil, fmt.Errorf("%w: MQTT varint too long", ErrMQTT)
		}
		multiplier *= 128
	}
	body := []byte{}
	if value > 0 {
		body = make([]byte, value)
		if _, err := io.ReadFull(conn, body); err != nil {
			if errors.Is(err, io.EOF) || errors.Is(err, io.ErrUnexpectedEOF) {
				return nil, nil
			}
			return nil, err
		}
	}
	_ = conn.SetReadDeadline(time.Time{})
	out := make([]byte, 1+len(varint)+len(body))
	out[0] = typeFlags
	copy(out[1:], varint)
	copy(out[1+len(varint):], body)
	return out, nil
}

// RendezvousSession bridges one MQTT rendezvous to a u32be-framed record stream.
type RendezvousSession struct {
	MQTT     *MqttSession
	SID      []byte
	Role     string
	OutTopic string
	InTopic  string
	Timeout  time.Duration

	mu        sync.Mutex
	closeOnce sync.Once
	local     net.Conn // handed to Connect handler
	peer      net.Conn // MQTT pump side
	cancel    context.CancelFunc
	wg        sync.WaitGroup
}

// NewRendezvousSession builds a PC or phone side rendezvous over mqtt.
func NewRendezvousSession(mqtt *MqttSession, sid []byte, role string, timeout time.Duration) (*RendezvousSession, error) {
	if role != "pc" && role != "phone" {
		return nil, fmt.Errorf("role must be 'pc' or 'phone'")
	}
	outSide := role
	inSide := "phone"
	if role == "phone" {
		inSide = "pc"
	}
	outTopic, err := RDVTopic(sid, outSide)
	if err != nil {
		return nil, err
	}
	inTopic, err := RDVTopic(sid, inSide)
	if err != nil {
		return nil, err
	}
	if timeout <= 0 {
		timeout = 8 * time.Second
	}
	sidCopy := append([]byte{}, sid...)
	return &RendezvousSession{
		MQTT:     mqtt,
		SID:      sidCopy,
		Role:     role,
		OutTopic: outTopic,
		InTopic:  inTopic,
		Timeout:  timeout,
	}, nil
}

// Open subscribes, starts pumps, and returns the local end of the record pipe.
func (r *RendezvousSession) Open(ctx context.Context) (net.Conn, error) {
	r.MQTT.OnMessage = r.onMessage
	subCtx, cancel := context.WithTimeout(ctx, r.Timeout)
	err := r.MQTT.Subscribe(subCtx, []string{r.InTopic}, 1)
	cancel()
	if err != nil {
		return nil, err
	}

	a, b := net.Pipe()
	r.mu.Lock()
	r.local = a
	r.peer = b
	loopCtx, loopCancel := context.WithCancel(ctx)
	r.cancel = loopCancel
	r.mu.Unlock()

	r.wg.Add(3)
	go func() {
		defer r.wg.Done()
		r.pumpOut(loopCtx)
	}()
	go func() {
		defer r.wg.Done()
		r.MQTT.ReaderLoop(loopCtx)
	}()
	go func() {
		defer r.wg.Done()
		r.MQTT.PingLoop(loopCtx)
	}()
	return a, nil
}

func (r *RendezvousSession) onMessage(topic string, payload []byte) {
	if topic != r.InTopic {
		return
	}
	r.mu.Lock()
	peer := r.peer
	r.mu.Unlock()
	if peer == nil || len(payload) > MaxRecord {
		return
	}
	frame := make([]byte, 4+len(payload))
	binary.BigEndian.PutUint32(frame[:4], uint32(len(payload)))
	copy(frame[4:], payload)
	_, _ = peer.Write(frame)
}

func (r *RendezvousSession) pumpOut(ctx context.Context) {
	defer r.Close()
	r.mu.Lock()
	peer := r.peer
	r.mu.Unlock()
	if peer == nil {
		return
	}
	for {
		select {
		case <-ctx.Done():
			return
		default:
		}
		header := make([]byte, 4)
		if _, err := io.ReadFull(peer, header); err != nil {
			return
		}
		length := binary.BigEndian.Uint32(header)
		if length > MaxRecord {
			return
		}
		payload := make([]byte, length)
		if length > 0 {
			if _, err := io.ReadFull(peer, payload); err != nil {
				return
			}
		}
		if err := r.MQTT.Publish(ctx, r.OutTopic, payload, 1); err != nil {
			return
		}
	}
}

// Close tears down the pipe, MQTT session, and background loops.
func (r *RendezvousSession) Close() {
	r.closeOnce.Do(func() {
		r.mu.Lock()
		if r.cancel != nil {
			r.cancel()
			r.cancel = nil
		}
		if r.local != nil {
			_ = r.local.Close()
			r.local = nil
		}
		if r.peer != nil {
			_ = r.peer.Close()
			r.peer = nil
		}
		r.mu.Unlock()
		r.MQTT.Close()
	})
}

// CloseWait closes and waits for background pumps (test helper).
func (r *RendezvousSession) CloseWait() {
	r.Close()
	done := make(chan struct{})
	go func() {
		r.wg.Wait()
		close(done)
	}()
	select {
	case <-done:
	case <-time.After(2 * time.Second):
	}
}
