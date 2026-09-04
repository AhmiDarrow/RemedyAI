package connect

import (
	"context"
	"encoding/binary"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"net"
	"strings"
	"sync"
	"time"
)

const (
	// HandshakeTimeout bounds the Noise IK exchange (Python HANDSHAKE_TIMEOUT_S).
	HandshakeTimeout = 20 * time.Second
	// IdleTimeout drops a silent authenticated socket (Python IDLE_TIMEOUT_S).
	IdleTimeout = 180 * time.Second
	// MaxBadRecords is the relay/rendezvous decrypt-fail budget.
	MaxBadRecords = 32
	// RekeyRecords triggers a send-cipher rekey after this many outbound records.
	RekeyRecords = 65536
	// RekeyInterval triggers a send-cipher rekey after this wall time.
	RekeyInterval = 15 * time.Minute
)

var (
	// ErrAuth is a pair/hello allowlist failure. Messages never contain secrets.
	ErrAuth = errors.New("connect auth failed")
	// ErrSession is a handshake/session orchestration failure.
	ErrSession = errors.New("connect session error")
	// ErrPaused means Connect is paused and must not accept a session.
	ErrPaused = errors.New("connect paused")
)

// SessionCrypto holds post-split transport ciphers for one Connect session.
type SessionCrypto struct {
	Send *CipherState
	Recv *CipherState

	mu     sync.Mutex
	sendN  int
	sendT0 time.Time
	Inner  bool
}

// NewSessionCrypto wraps send/recv CipherStates.
func NewSessionCrypto(send, recv *CipherState) *SessionCrypto {
	return &SessionCrypto{Send: send, Recv: recv, sendT0: time.Now()}
}

// NoteSend increments the outbound counter. True when the caller should rekey.
func (c *SessionCrypto) NoteSend() bool {
	if c == nil {
		return false
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	c.sendN++
	if c.sendN >= RekeyRecords || time.Since(c.sendT0) >= RekeyInterval {
		c.sendN = 0
		c.sendT0 = time.Now()
		return true
	}
	return false
}

// Encrypt packs a transport record under the send cipher (AD empty).
func (c *SessionCrypto) Encrypt(plaintext []byte) ([]byte, error) {
	if c == nil || c.Send == nil {
		return nil, fmt.Errorf("%w: nil send cipher", ErrSession)
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	return EncryptRecord(c.Send, plaintext)
}

// Decrypt unpacks and authenticates a packed transport record with the recv cipher.
func (c *SessionCrypto) Decrypt(blob []byte) ([]byte, error) {
	if c == nil || c.Recv == nil {
		return nil, fmt.Errorf("%w: nil recv cipher", ErrSession)
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	return DecryptRecord(c.Recv, blob)
}

// RekeySend runs CipherState.Rekey on the send side (after an inner TYPE_REKEY).
func (c *SessionCrypto) RekeySend() error {
	if c == nil || c.Send == nil {
		return fmt.Errorf("%w: nil send cipher", ErrSession)
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.Send.Rekey()
}

// RekeyRecv runs CipherState.Rekey on the recv side (peer sent TYPE_REKEY).
func (c *SessionCrypto) RekeyRecv() error {
	if c == nil || c.Recv == nil {
		return fmt.Errorf("%w: nil recv cipher", ErrSession)
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.Recv.Rekey()
}

// ReadLenPrefixed reads u32be length + body (Noise handshake messages).
func ReadLenPrefixed(r io.Reader) ([]byte, error) {
	var header [4]byte
	if _, err := io.ReadFull(r, header[:]); err != nil {
		return nil, err
	}
	length := binary.BigEndian.Uint32(header[:])
	if length < 1 || length > MaxRecord {
		return nil, fmt.Errorf("%w: invalid handshake length", ErrSession)
	}
	body := make([]byte, length)
	if _, err := io.ReadFull(r, body); err != nil {
		return nil, err
	}
	return body, nil
}

// WriteLenPrefixed writes u32be(len(body)) || body.
func WriteLenPrefixed(w io.Writer, body []byte) error {
	if len(body) < 1 || len(body) > MaxRecord {
		return fmt.Errorf("%w: invalid handshake length", ErrSession)
	}
	var header [4]byte
	binary.BigEndian.PutUint32(header[:], uint32(len(body)))
	if _, err := w.Write(header[:]); err != nil {
		return err
	}
	_, err := w.Write(body)
	return err
}

// HandshakeResponder runs Noise_IK as the host. Returns crypto, first payload, device static.
func HandshakeResponder(conn net.Conn, hostKP KeyPair) (*SessionCrypto, []byte, []byte, error) {
	if conn == nil {
		return nil, nil, nil, fmt.Errorf("%w: nil conn", ErrSession)
	}
	hs, err := NewHandshakeState(HandshakeConfig{
		Initiator: false,
		Local:     hostKP,
	})
	if err != nil {
		return nil, nil, nil, err
	}
	raw, err := ReadLenPrefixed(conn)
	if err != nil {
		return nil, nil, nil, err
	}
	payload, err := hs.ReadMessage(raw)
	if err != nil {
		return nil, nil, nil, err
	}
	out, err := hs.WriteMessage(nil)
	if err != nil {
		return nil, nil, nil, err
	}
	if err := WriteLenPrefixed(conn, out); err != nil {
		return nil, nil, nil, err
	}
	send, recv, err := hs.Split()
	if err != nil {
		return nil, nil, nil, err
	}
	return NewSessionCrypto(send, recv), payload, hs.RemoteStaticPublic(), nil
}

// HandshakeInitiator runs Noise_IK as the phone (tests / client helpers).
func HandshakeInitiator(conn net.Conn, deviceKP KeyPair, hostPub, payload []byte) (*SessionCrypto, error) {
	if conn == nil {
		return nil, fmt.Errorf("%w: nil conn", ErrSession)
	}
	hs, err := NewHandshakeState(HandshakeConfig{
		Initiator:    true,
		Local:        deviceKP,
		RemoteStatic: hostPub,
	})
	if err != nil {
		return nil, err
	}
	msg0, err := hs.WriteMessage(payload)
	if err != nil {
		return nil, err
	}
	if err := WriteLenPrefixed(conn, msg0); err != nil {
		return nil, err
	}
	raw, err := ReadLenPrefixed(conn)
	if err != nil {
		return nil, err
	}
	if _, err := hs.ReadMessage(raw); err != nil {
		return nil, err
	}
	send, recv, err := hs.Split()
	if err != nil {
		return nil, err
	}
	return NewSessionCrypto(send, recv), nil
}

// AuthenticatePayload resolves pair or hello against the device store (fail closed).
func AuthenticatePayload(payload, devicePub []byte, home string) (*Device, error) {
	kind, fields, err := ParseHandshakePayload(payload)
	if err != nil {
		return nil, fmt.Errorf("%w: %v", ErrAuth, err)
	}
	if kind == "pair" {
		if len(devicePub) != DHLen {
			return nil, fmt.Errorf("%w: missing initiator static", ErrAuth)
		}
		deviceID, err := CompletePair(fields.Secret, devicePub, fields.Name, home)
		if err == nil {
			rec, gerr := GetDevice(deviceID, home)
			if gerr != nil || rec == nil {
				return nil, fmt.Errorf("%w: pair failed", ErrAuth)
			}
			return rec, nil
		}
		// Noise already proved the device static. An allowlisted phone may
		// reconnect after the one-use QR secret is consumed.
		rec, ferr := FindDeviceByPublic(hex.EncodeToString(devicePub), home)
		if ferr == nil && rec != nil && !rec.Revoked {
			return rec, nil
		}
		return nil, fmt.Errorf("%w: %v", ErrAuth, err)
	}

	deviceID := fields.DeviceID
	rec, err := GetDevice(deviceID, home)
	if err != nil || rec == nil || rec.Revoked {
		return nil, fmt.Errorf("%w: not allowlisted", ErrAuth)
	}
	want := strings.ToLower(strings.TrimSpace(rec.PublicHex))
	got := ""
	if len(devicePub) == DHLen {
		got = hex.EncodeToString(devicePub)
	}
	if want == "" || got == "" || got != want {
		return nil, fmt.Errorf("%w: static mismatch", ErrAuth)
	}
	return rec, nil
}

// Session is an authenticated Noise transport over a TCP connection.
type Session struct {
	Conn   net.Conn
	Crypto *SessionCrypto
	Device Device
	Home   string

	mu             sync.Mutex
	writeMu        sync.Mutex
	lenientDecrypt bool
	badRecords     int
}

// SendPlain encrypts plaintext and writes one transport record.
func (s *Session) SendPlain(plaintext []byte) error {
	return s.sendPlain(plaintext, false)
}

// SendPlainInner is SendPlain with post-send TYPE_REKEY when NoteSend is due
// (matches Python inner_rekey=True used for PING/PONG and inner HTTP).
func (s *Session) SendPlainInner(plaintext []byte) error {
	return s.sendPlain(plaintext, true)
}

func (s *Session) sendPlain(plaintext []byte, innerRekey bool) error {
	if s == nil || s.Crypto == nil || s.Conn == nil {
		return fmt.Errorf("%w: nil session", ErrSession)
	}
	s.writeMu.Lock()
	defer s.writeMu.Unlock()
	blob, err := s.Crypto.Encrypt(plaintext)
	if err != nil {
		return err
	}
	if err := WriteRecord(s.Conn, blob); err != nil {
		return err
	}
	if !s.Crypto.NoteSend() || !innerRekey {
		return nil
	}
	frame, err := EncodeInner(TypeRekey, 0, nil, true)
	if err != nil {
		return err
	}
	blob, err = s.Crypto.Encrypt(frame)
	if err != nil {
		return err
	}
	if err := WriteRecord(s.Conn, blob); err != nil {
		return err
	}
	return s.Crypto.RekeySend()
}

// HandleInnerControl demuxes PING / PONG / REKEY on one decrypted record.
// HTTP request dispatch lands in a later slice; those frames are ignored here.
func HandleInnerControl(sess *Session, plain []byte) error {
	if sess == nil || sess.Crypto == nil {
		return fmt.Errorf("%w: nil session", ErrSession)
	}
	if len(plain) == 0 || plain[0] != InnerVersion {
		return nil
	}
	sess.Crypto.Inner = true
	frame, err := DecodeInner(plain)
	if err != nil {
		return err
	}
	switch frame.Type {
	case TypeRekey:
		return sess.Crypto.RekeyRecv()
	case TypePing:
		pong, err := EncodeInner(TypePong, frame.ID, nil, true)
		if err != nil {
			return err
		}
		return sess.SendPlainInner(pong)
	case TypePong:
		return nil
	default:
		return nil
	}
}

// ReadTransport reads one packed transport record from the socket.
func (s *Session) ReadTransport() ([]byte, error) {
	if s == nil || s.Conn == nil {
		return nil, fmt.Errorf("%w: nil session", ErrSession)
	}
	nonce, ct, err := ReadRecord(s.Conn)
	if err != nil {
		return nil, err
	}
	return PackRecord(nonce, ct)
}

// RecvPlain reads and decrypts one transport record.
func (s *Session) RecvPlain() ([]byte, error) {
	if s == nil || s.Crypto == nil {
		return nil, fmt.Errorf("%w: nil session", ErrSession)
	}
	for {
		blob, err := s.ReadTransport()
		if err != nil {
			return nil, err
		}
		plain, err := s.Crypto.Decrypt(blob)
		if err == nil {
			s.mu.Lock()
			s.badRecords = 0
			s.mu.Unlock()
			return plain, nil
		}
		s.mu.Lock()
		s.badRecords++
		bad := s.badRecords
		lenient := s.lenientDecrypt
		s.mu.Unlock()
		if !lenient || bad > MaxBadRecords {
			return nil, err
		}
	}
}

// SessionConfig configures AcceptSession / SessionConnHandler.
type SessionConfig struct {
	Home             string
	HostKP           KeyPair // zero private → LoadOrCreateHostKeyPair(Home)
	HandshakeTimeout time.Duration
	IdleTimeout      time.Duration
	LenientDecrypt   bool
	ShouldStop       func() bool
	OnDevice         func(Device)
	// OnAuthed runs after auth. Nil → AcceptSession returns immediately.
	OnAuthed func(ctx context.Context, sess *Session) error
	// Listener, when set, receives BindDevice after successful auth.
	Listener *Listener
}

// AcceptSession runs pause check → Noise handshake → allowlist auth.
func AcceptSession(ctx context.Context, conn net.Conn, cfg SessionConfig) (*Session, error) {
	if conn == nil {
		return nil, fmt.Errorf("%w: nil conn", ErrSession)
	}
	if IsPaused(cfg.Home) {
		return nil, ErrPaused
	}

	hostKP := cfg.HostKP
	if len(hostKP.Private) != DHLen {
		kp, err := LoadOrCreateHostKeyPair(cfg.Home)
		if err != nil {
			return nil, err
		}
		hostKP = kp
	}

	hsTimeout := cfg.HandshakeTimeout
	if hsTimeout <= 0 {
		hsTimeout = HandshakeTimeout
	}
	deadline, hasDeadline := ctx.Deadline()
	hsDeadline := time.Now().Add(hsTimeout)
	if hasDeadline && deadline.Before(hsDeadline) {
		hsDeadline = deadline
	}
	_ = conn.SetDeadline(hsDeadline)

	crypto, payload, devicePub, err := HandshakeResponder(conn, hostKP)
	_ = conn.SetDeadline(time.Time{})
	if err != nil {
		return nil, err
	}
	if IsPaused(cfg.Home) {
		return nil, ErrPaused
	}

	device, err := AuthenticatePayload(payload, devicePub, cfg.Home)
	if err != nil {
		return nil, err
	}
	if cfg.OnDevice != nil {
		cfg.OnDevice(*device)
	}
	if cfg.Listener != nil {
		cfg.Listener.BindDevice(conn, device.ID)
	}

	sess := &Session{
		Conn:           conn,
		Crypto:         crypto,
		Device:         *device,
		Home:           cfg.Home,
		lenientDecrypt: cfg.LenientDecrypt,
	}
	return sess, nil
}

// RunSession accepts a session then runs OnAuthed, or waits until idle/stop.
// The listener closes the socket when the handler returns, so this blocks for
// the session lifetime (HTTP/inner dispatch lands in a later slice).
func RunSession(ctx context.Context, conn net.Conn, cfg SessionConfig) (*Session, error) {
	sess, err := AcceptSession(ctx, conn, cfg)
	if err != nil {
		return nil, err
	}
	if cfg.OnAuthed != nil {
		if err := cfg.OnAuthed(ctx, sess); err != nil {
			return sess, err
		}
		return sess, nil
	}
	return sess, runIdleLoop(ctx, sess, cfg)
}

func runIdleLoop(ctx context.Context, sess *Session, cfg SessionConfig) error {
	idle := cfg.IdleTimeout
	if idle <= 0 {
		idle = IdleTimeout
	}
	for {
		if cfg.ShouldStop != nil && cfg.ShouldStop() {
			return nil
		}
		if IsPaused(cfg.Home) {
			return nil
		}
		select {
		case <-ctx.Done():
			return ctx.Err()
		default:
		}
		_ = sess.Conn.SetReadDeadline(time.Now().Add(idle))
		plain, err := sess.RecvPlain()
		_ = sess.Conn.SetReadDeadline(time.Time{})
		if err != nil {
			var ne net.Error
			if errors.As(err, &ne) && ne.Timeout() {
				return nil
			}
			if errors.Is(err, io.EOF) || errors.Is(err, net.ErrClosed) {
				return nil
			}
			return err
		}
		if err := HandleInnerControl(sess, plain); err != nil {
			return err
		}
	}
}

// SessionConnHandler returns a Listener ConnHandler that runs RunSession.
func SessionConnHandler(cfg SessionConfig) ConnHandler {
	return func(ctx context.Context, conn net.Conn) {
		_, _ = RunSession(ctx, conn, cfg)
	}
}
