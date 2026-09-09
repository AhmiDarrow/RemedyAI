package gateway

import (
	"bufio"
	"crypto/rand"
	"crypto/sha1"
	"encoding/base64"
	"encoding/binary"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"strings"
	"sync"
	"time"
)

// wsConn is a minimal RFC6455 client (text/close/ping/pong) for Discord/Slack.
// Writes are serialized so a heartbeat goroutine and the session loop never
// interleave frame bytes.
type wsConn struct {
	conn   net.Conn
	reader *bufio.Reader
	wmu    sync.Mutex
}

// dialWS is a var so gateway tests can drive a real WS peer over net.Pipe
// instead of reaching Discord / Slack.
var dialWS = func(rawURL string, headers http.Header, timeout time.Duration) (*wsConn, error) {
	u, err := url.Parse(rawURL)
	if err != nil {
		return nil, err
	}
	scheme := "ws"
	switch u.Scheme {
	case "wss", "https":
		scheme = "wss"
	case "ws", "http":
		scheme = "ws"
	default:
		return nil, fmt.Errorf("unsupported ws scheme %q", u.Scheme)
	}
	host := u.Host
	if !strings.Contains(host, ":") {
		if scheme == "wss" {
			host += ":443"
		} else {
			host += ":80"
		}
	}
	dialer := net.Dialer{Timeout: timeout}
	var conn net.Conn
	if scheme == "wss" {
		conn, err = tlsDial(dialer, host, u.Hostname())
	} else {
		conn, err = dialer.Dial("tcp", host)
	}
	if err != nil {
		return nil, err
	}
	_ = conn.SetDeadline(time.Now().Add(timeout))

	var keyBytes [16]byte
	_, _ = rand.Read(keyBytes[:])
	key := base64.StdEncoding.EncodeToString(keyBytes[:])

	path := u.RequestURI()
	if path == "" {
		path = "/"
	}
	req := "GET " + path + " HTTP/1.1\r\n"
	req += "Host: " + u.Host + "\r\n"
	req += "Upgrade: websocket\r\n"
	req += "Connection: Upgrade\r\n"
	req += "Sec-WebSocket-Key: " + key + "\r\n"
	req += "Sec-WebSocket-Version: 13\r\n"
	for k, vals := range headers {
		for _, v := range vals {
			req += k + ": " + v + "\r\n"
		}
	}
	req += "\r\n"
	if _, err := io.WriteString(conn, req); err != nil {
		_ = conn.Close()
		return nil, err
	}
	br := bufio.NewReader(conn)
	status, err := br.ReadString('\n')
	if err != nil {
		_ = conn.Close()
		return nil, err
	}
	if !strings.Contains(status, "101") {
		_ = conn.Close()
		return nil, fmt.Errorf("websocket handshake: %s", strings.TrimSpace(status))
	}
	acceptOK := false
	want := wsAccept(key)
	for {
		line, err := br.ReadString('\n')
		if err != nil {
			_ = conn.Close()
			return nil, err
		}
		line = strings.TrimSpace(line)
		if line == "" {
			break
		}
		if strings.HasPrefix(strings.ToLower(line), "sec-websocket-accept:") {
			got := strings.TrimSpace(line[len("sec-websocket-accept:"):])
			acceptOK = got == want
		}
	}
	if !acceptOK {
		_ = conn.Close()
		return nil, errors.New("websocket accept mismatch")
	}
	_ = conn.SetDeadline(time.Time{})
	return &wsConn{conn: conn, reader: br}, nil
}

func wsAccept(key string) string {
	const guid = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
	sum := sha1.Sum([]byte(key + guid))
	return base64.StdEncoding.EncodeToString(sum[:])
}

func (w *wsConn) Close() error {
	if w == nil || w.conn == nil {
		return nil
	}
	_ = w.writeFrame(0x8, nil) // close
	return w.conn.Close()
}

func (w *wsConn) WriteText(msg []byte) error {
	return w.writeFrame(0x1, msg)
}

func (w *wsConn) writeFrame(opcode byte, payload []byte) error {
	if w == nil || w.conn == nil {
		return errors.New("ws closed")
	}
	w.wmu.Lock()
	defer w.wmu.Unlock()
	var hdr []byte
	hdr = append(hdr, 0x80|opcode)
	n := len(payload)
	maskBit := byte(0x80)
	switch {
	case n < 126:
		hdr = append(hdr, maskBit|byte(n))
	case n < 65536:
		hdr = append(hdr, maskBit|126, byte(n>>8), byte(n))
	default:
		var b [8]byte
		binary.BigEndian.PutUint64(b[:], uint64(n))
		hdr = append(hdr, maskBit|127)
		hdr = append(hdr, b[:]...)
	}
	var mask [4]byte
	_, _ = rand.Read(mask[:])
	hdr = append(hdr, mask[:]...)
	masked := make([]byte, n)
	for i := 0; i < n; i++ {
		masked[i] = payload[i] ^ mask[i%4]
	}
	if _, err := w.conn.Write(hdr); err != nil {
		return err
	}
	_, err := w.conn.Write(masked)
	return err
}

// ReadMessage returns opcode and payload (handles ping automatically).
func (w *wsConn) ReadMessage() (opcode byte, payload []byte, err error) {
	for {
		op, data, err := w.readFrame()
		if err != nil {
			return 0, nil, err
		}
		switch op {
		case 0x9: // ping
			_ = w.writeFrame(0xA, data)
			continue
		case 0xA: // pong
			continue
		case 0x8:
			return 0x8, data, io.EOF
		default:
			return op, data, nil
		}
	}
}

func (w *wsConn) readFrame() (opcode byte, payload []byte, err error) {
	var h [2]byte
	if _, err := io.ReadFull(w.reader, h[:]); err != nil {
		return 0, nil, err
	}
	opcode = h[0] & 0x0f
	masked := h[1]&0x80 != 0
	n := int(h[1] & 0x7f)
	switch n {
	case 126:
		var ext [2]byte
		if _, err := io.ReadFull(w.reader, ext[:]); err != nil {
			return 0, nil, err
		}
		n = int(binary.BigEndian.Uint16(ext[:]))
	case 127:
		var ext [8]byte
		if _, err := io.ReadFull(w.reader, ext[:]); err != nil {
			return 0, nil, err
		}
		n = int(binary.BigEndian.Uint64(ext[:]))
	}
	if n < 0 || n > 8<<20 {
		return 0, nil, errors.New("ws frame too large")
	}
	var mask [4]byte
	if masked {
		if _, err := io.ReadFull(w.reader, mask[:]); err != nil {
			return 0, nil, err
		}
	}
	payload = make([]byte, n)
	if _, err := io.ReadFull(w.reader, payload); err != nil {
		return 0, nil, err
	}
	if masked {
		for i := 0; i < n; i++ {
			payload[i] ^= mask[i%4]
		}
	}
	return opcode, payload, nil
}
