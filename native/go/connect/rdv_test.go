package connect_test

import (
	"bytes"
	"context"
	"crypto/rand"
	"encoding/binary"
	"encoding/hex"
	"errors"
	"io"
	"net"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/AhmiDarrow/RemedyAI/native/go/connect"
)

func TestVarintRoundtrip(t *testing.T) {
	for _, n := range []int{0, 1, 127, 128, 16383, 16384, 2097151, 268435455} {
		enc, err := connect.EncodeVarint(n)
		if err != nil {
			t.Fatalf("encode %d: %v", n, err)
		}
		value, consumed, err := connect.DecodeVarint(enc, 0)
		if err != nil {
			t.Fatalf("decode %d: %v", n, err)
		}
		if value != n || consumed != len(enc) {
			t.Fatalf("n=%d value=%d consumed=%d len=%d", n, value, consumed, len(enc))
		}
	}
}

func TestVarintOutOfRange(t *testing.T) {
	if _, err := connect.EncodeVarint(-1); err == nil {
		t.Fatal("expected error for -1")
	}
	if _, err := connect.EncodeVarint(268435456); err == nil {
		t.Fatal("expected error for too large")
	}
}

func TestVarintKnownEncodings(t *testing.T) {
	cases := map[int]string{
		0:     "00",
		127:   "7f",
		128:   "8001",
		16384: "808001",
	}
	for n, want := range cases {
		enc, err := connect.EncodeVarint(n)
		if err != nil {
			t.Fatal(err)
		}
		if got := hex.EncodeToString(enc); got != want {
			t.Fatalf("%d: got %s want %s", n, got, want)
		}
	}
}

func TestMqttConnackCodes(t *testing.T) {
	code, err := connect.ParseConnack([]byte{0x20, 0x02, 0x00, 0x00})
	if err != nil || code != 0 {
		t.Fatalf("got %d %v", code, err)
	}
	code, err = connect.ParseConnack([]byte{0x20, 0x02, 0x00, 0x05})
	if err != nil || code != 5 {
		t.Fatalf("got %d %v", code, err)
	}
	if _, err := connect.ParseConnack([]byte{0x10, 0x00}); err == nil {
		t.Fatal("expected not a CONNACK")
	}
}

func TestMqttSubscribeSuback(t *testing.T) {
	pkt, err := connect.BuildSubscribe(7, []string{"a/b", "c/d"}, 1)
	if err != nil {
		t.Fatal(err)
	}
	if pkt[0] != 0x82 {
		t.Fatalf("fixed header %#x", pkt[0])
	}
	if want := "820e00070003612f62010003632f6401"; hex.EncodeToString(pkt) != want {
		t.Fatalf("got %s want %s", hex.EncodeToString(pkt), want)
	}
	pid, codes, err := connect.ParseSuback([]byte{0x90, 0x03, 0x00, 0x07, 0x01})
	if err != nil {
		t.Fatal(err)
	}
	if pid != 7 || len(codes) != 1 || codes[0] != 1 {
		t.Fatalf("pid=%d codes=%v", pid, codes)
	}
}

func TestMqttPublishParseQoS1(t *testing.T) {
	sid := make([]byte, 16)
	_, _ = rand.Read(sid)
	topic := "remedy/" + hex.EncodeToString(sid) + "/pc"
	pkt, err := connect.BuildPublish(9, topic, []byte("xyz"), 1)
	if err != nil {
		t.Fatal(err)
	}
	msg, err := connect.ParsePublish(pkt)
	if err != nil {
		t.Fatal(err)
	}
	if msg.Topic != topic || !bytes.Equal(msg.Payload, []byte("xyz")) || msg.QoS != 1 || msg.PacketID != 9 {
		t.Fatalf("%+v", msg)
	}
}

func TestMqttPublishParseQoS0(t *testing.T) {
	pkt, err := connect.BuildPublish(0, "t", []byte("v"), 0)
	if err != nil {
		t.Fatal(err)
	}
	if want := "300400017476"; hex.EncodeToString(pkt) != want {
		t.Fatalf("got %s", hex.EncodeToString(pkt))
	}
	msg, err := connect.ParsePublish(pkt)
	if err != nil {
		t.Fatal(err)
	}
	if msg.QoS != 0 || !bytes.Equal(msg.Payload, []byte("v")) || msg.PacketID != 0 {
		t.Fatalf("%+v", msg)
	}
}

func TestMqttPublishParseRejectsBad(t *testing.T) {
	if _, err := connect.ParsePublish([]byte{0x00, 0x00}); err == nil {
		t.Fatal("expected reject")
	}
	if _, err := connect.ParsePublish([]byte{0x36, 0x02, 0x00, 0x01}); err == nil {
		t.Fatal("expected QoS 3 reject")
	}
}

func TestMqttPubackPacket(t *testing.T) {
	got := connect.BuildPuback(0x1234)
	if !bytes.Equal(got, []byte{0x40, 0x02, 0x12, 0x34}) {
		t.Fatalf("%x", got)
	}
}

func TestMqttBuildConnectMatchesPython(t *testing.T) {
	pkt, err := connect.BuildConnect("abc", 30)
	if err != nil {
		t.Fatal(err)
	}
	if want := "100f00044d5154540402001e0003616263"; hex.EncodeToString(pkt) != want {
		t.Fatalf("got %s want %s", hex.EncodeToString(pkt), want)
	}
}

func TestRDVQRValueRoundtrip(t *testing.T) {
	value := connect.RDVQRValue()
	if !strings.Contains(value, ";") {
		t.Fatalf("expected ; in %q", value)
	}
	eps, err := connect.ParseRDVEndpoints(value)
	if err != nil {
		t.Fatal(err)
	}
	if len(eps) != len(connect.PublicRDVEndpoints) {
		t.Fatalf("len=%d", len(eps))
	}
	for i, ep := range connect.PublicRDVEndpoints {
		if eps[i].Host != ep.Host || eps[i].Port != ep.Port {
			t.Fatalf("%d: got %+v want %+v", i, eps[i], ep)
		}
	}
	if _, err := connect.ParseRDVEndpoints(""); err == nil {
		t.Fatal("expected empty error")
	}
	if _, err := connect.ParseRDVEndpoints(";;;"); err == nil {
		t.Fatal("expected empty error")
	}
}

func TestRDVTopicShape(t *testing.T) {
	sid := make([]byte, 16)
	for i := range sid {
		sid[i] = byte(i)
	}
	pc, err := connect.RDVTopic(sid, "pc")
	if err != nil {
		t.Fatal(err)
	}
	if pc != "remedy/"+hex.EncodeToString(sid)+"/pc" {
		t.Fatal(pc)
	}
	phone, err := connect.RDVTopic(sid, "phone")
	if err != nil {
		t.Fatal(err)
	}
	if phone != "remedy/"+hex.EncodeToString(sid)+"/phone" {
		t.Fatal(phone)
	}
	if _, err := connect.RDVTopic(sid, "bogus"); err == nil {
		t.Fatal("expected side error")
	}
	if _, err := connect.RDVTopic([]byte("short"), "pc"); err == nil {
		t.Fatal("expected length error")
	}
}

// ---------------------------------------------------------------------------
// In-process fake broker (minimal MQTT 3.1.1).
// ---------------------------------------------------------------------------

func readMQTTPacket(conn net.Conn) ([]byte, error) {
	first := make([]byte, 1)
	if _, err := io.ReadFull(conn, first); err != nil {
		return nil, err
	}
	varint := make([]byte, 0, 4)
	multiplier := 1
	value := 0
	for {
		b := make([]byte, 1)
		if _, err := io.ReadFull(conn, b); err != nil {
			return nil, err
		}
		varint = append(varint, b[0])
		value += int(b[0]&0x7F) * multiplier
		if b[0]&0x80 == 0 {
			break
		}
		multiplier *= 128
	}
	body := []byte{}
	if value > 0 {
		body = make([]byte, value)
		if _, err := io.ReadFull(conn, body); err != nil {
			return nil, err
		}
	}
	out := make([]byte, 1+len(varint)+len(body))
	out[0] = first[0]
	copy(out[1:], varint)
	copy(out[1+len(varint):], body)
	return out, nil
}

func subTopics(pkt []byte) []string {
	_, consumed, err := connect.DecodeVarint(pkt, 1)
	if err != nil {
		return nil
	}
	body := pkt[1+consumed:]
	offset := 2
	var topics []string
	for offset+3 <= len(body) {
		tlen := int(binary.BigEndian.Uint16(body[offset : offset+2]))
		offset += 2
		topics = append(topics, string(body[offset:offset+tlen]))
		offset += tlen + 1
	}
	return topics
}

type fakeBroker struct {
	connackCode int
	mu          sync.Mutex
	subs        map[string]map[net.Conn]struct{}
	ln          net.Listener
}

func startFakeBroker(t *testing.T, connackCode int) *fakeBroker {
	t.Helper()
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	b := &fakeBroker{
		connackCode: connackCode,
		subs:        map[string]map[net.Conn]struct{}{},
		ln:          ln,
	}
	go b.serve()
	t.Cleanup(func() { _ = b.stop() })
	return b
}

func (b *fakeBroker) port() int {
	return b.ln.Addr().(*net.TCPAddr).Port
}

func (b *fakeBroker) stop() error {
	return b.ln.Close()
}

func (b *fakeBroker) serve() {
	for {
		conn, err := b.ln.Accept()
		if err != nil {
			return
		}
		go b.handle(conn)
	}
}

func (b *fakeBroker) handle(conn net.Conn) {
	defer func() {
		b.mu.Lock()
		for topic, writers := range b.subs {
			delete(writers, conn)
			if len(writers) == 0 {
				delete(b.subs, topic)
			}
		}
		b.mu.Unlock()
		_ = conn.Close()
	}()
	if _, err := readMQTTPacket(conn); err != nil { // CONNECT
		return
	}
	ack := []byte{0x20, 0x02, 0x00, byte(b.connackCode)}
	if _, err := conn.Write(ack); err != nil {
		return
	}
	if b.connackCode != 0 {
		return
	}
	for {
		pkt, err := readMQTTPacket(conn)
		if err != nil {
			return
		}
		kind := pkt[0] & 0xF0
		switch kind {
		case 0x80: // SUBSCRIBE
			topics := subTopics(pkt)
			pid := binary.BigEndian.Uint16(pkt[2:4])
			b.mu.Lock()
			for _, topic := range topics {
				if b.subs[topic] == nil {
					b.subs[topic] = map[net.Conn]struct{}{}
				}
				b.subs[topic][conn] = struct{}{}
			}
			b.mu.Unlock()
			body := make([]byte, 2+len(topics))
			binary.BigEndian.PutUint16(body[:2], pid)
			for i := range topics {
				body[2+i] = 0x01
			}
			out := append([]byte{0x90, byte(len(body))}, body...)
			if _, err := conn.Write(out); err != nil {
				return
			}
		case 0x30: // PUBLISH
			msg, err := connect.ParsePublish(pkt)
			if err != nil {
				return
			}
			if msg.QoS > 0 {
				if _, err := conn.Write(connect.BuildPuback(msg.PacketID)); err != nil {
					return
				}
			}
			b.mu.Lock()
			subs := make([]net.Conn, 0, len(b.subs[msg.Topic]))
			for c := range b.subs[msg.Topic] {
				if c != conn {
					subs = append(subs, c)
				}
			}
			b.mu.Unlock()
			for _, sub := range subs {
				_, _ = sub.Write(pkt)
			}
		case 0xC0: // PINGREQ
			if _, err := conn.Write([]byte{0xd0, 0x00}); err != nil {
				return
			}
		case 0xE0: // DISCONNECT
			return
		}
	}
}

func makeRDVSession(t *testing.T, port int, sid []byte, role string) (*connect.RendezvousSession, net.Conn) {
	t.Helper()
	mqtt, err := connect.NewMqttSession("127.0.0.1", port, 5*time.Second)
	if err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	if err := mqtt.Connect(ctx); err != nil {
		t.Fatal(err)
	}
	session, err := connect.NewRendezvousSession(mqtt, sid, role, 5*time.Second)
	if err != nil {
		t.Fatal(err)
	}
	conn, err := session.Open(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	return session, conn
}

func TestRDVRoundtripPCPhone(t *testing.T) {
	broker := startFakeBroker(t, 0)
	sid := make([]byte, 16)
	_, _ = rand.Read(sid)

	pc, pcConn := makeRDVSession(t, broker.port(), sid, "pc")
	defer pc.CloseWait()
	phone, phConn := makeRDVSession(t, broker.port(), sid, "phone")
	defer phone.CloseWait()

	// Give subscribe/reader loops a moment to arm.
	time.Sleep(50 * time.Millisecond)

	payload := append([]byte("hello-from-pc-"), make([]byte, 32)...)
	_, _ = rand.Read(payload[len(payload)-32:])
	frame := make([]byte, 4+len(payload))
	binary.BigEndian.PutUint32(frame[:4], uint32(len(payload)))
	copy(frame[4:], payload)
	if _, err := pcConn.Write(frame); err != nil {
		t.Fatal(err)
	}
	got := make([]byte, 4+len(payload))
	_ = phConn.SetReadDeadline(time.Now().Add(5 * time.Second))
	if _, err := io.ReadFull(phConn, got); err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(got[4:], payload) {
		t.Fatal("pc→phone mismatch")
	}

	reply := append([]byte("reply-from-phone-"), make([]byte, 48)...)
	_, _ = rand.Read(reply[len(reply)-48:])
	frame2 := make([]byte, 4+len(reply))
	binary.BigEndian.PutUint32(frame2[:4], uint32(len(reply)))
	copy(frame2[4:], reply)
	if _, err := phConn.Write(frame2); err != nil {
		t.Fatal(err)
	}
	got2 := make([]byte, 4+len(reply))
	_ = pcConn.SetReadDeadline(time.Now().Add(5 * time.Second))
	if _, err := io.ReadFull(pcConn, got2); err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(got2[4:], reply) {
		t.Fatal("phone→pc mismatch")
	}

	big := make([]byte, 300)
	_, _ = rand.Read(big)
	frame3 := make([]byte, 4+len(big))
	binary.BigEndian.PutUint32(frame3[:4], uint32(len(big)))
	copy(frame3[4:], big)
	if _, err := pcConn.Write(frame3); err != nil {
		t.Fatal(err)
	}
	got3 := make([]byte, 4+len(big))
	_ = phConn.SetReadDeadline(time.Now().Add(5 * time.Second))
	if _, err := io.ReadFull(phConn, got3); err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(got3[4:], big) {
		t.Fatal("large record mismatch")
	}
}

func TestRDVWrongSIDDoesNotMeet(t *testing.T) {
	broker := startFakeBroker(t, 0)
	sidA := make([]byte, 16)
	sidB := make([]byte, 16)
	_, _ = rand.Read(sidA)
	_, _ = rand.Read(sidB)

	a, aConn := makeRDVSession(t, broker.port(), sidA, "pc")
	defer a.CloseWait()
	b, bConn := makeRDVSession(t, broker.port(), sidB, "phone")
	defer b.CloseWait()
	time.Sleep(50 * time.Millisecond)

	frame := []byte{0, 0, 0, 5, 'a', 'a', 'a', 'a', 'a'}
	if _, err := aConn.Write(frame); err != nil {
		t.Fatal(err)
	}
	_ = bConn.SetReadDeadline(time.Now().Add(600 * time.Millisecond))
	buf := make([]byte, 9)
	_, err := io.ReadFull(bConn, buf)
	if err == nil {
		t.Fatal("different session ids must not rendezvous")
	}
}

func TestMQTTConnectRefused(t *testing.T) {
	broker := startFakeBroker(t, 5)
	mqtt, err := connect.NewMqttSession("127.0.0.1", broker.port(), 3*time.Second)
	if err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	err = mqtt.Connect(ctx)
	if err == nil || !errors.Is(err, connect.ErrMQTT) && !containsMQTTRefuse(err) {
		t.Fatalf("expected MQTT refuse, got %v", err)
	}
}

func containsMQTTRefuse(err error) bool {
	return err != nil && (errors.Is(err, connect.ErrMQTT) ||
		bytes.Contains([]byte(err.Error()), []byte("refused")))
}

func TestRDVBrokerDownIsGraceful(t *testing.T) {
	mqtt, err := connect.NewMqttSession("127.0.0.1", 1, time.Second)
	if err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	err = mqtt.Connect(ctx)
	if err == nil {
		t.Fatal("expected dial failure")
	}
}
