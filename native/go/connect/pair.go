package connect

import (
	"crypto/rand"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/base64"
	"encoding/hex"
	"fmt"
	"strings"
	"sync"
	"time"
)

const (
	// PairTTL is the one-shot QR lifetime.
	PairTTL = 60 * time.Second
	// PairSecretLen is the raw pair-secret byte length.
	PairSecretLen = 32
	// QRVersion is the first line of a pairing QR.
	QRVersion = "remedy-connect/1"
)

var (
	pairMu      sync.Mutex
	pendingPair *pending
	// nowFunc is overridable in tests (expired QR).
	nowFunc = time.Now
)

type pending struct {
	secret []byte
	exp    time.Time
	used   bool
}

// PairStartOpts configures StartPair.
type PairStartOpts struct {
	Loopback  bool
	BindHost  string
	BindPort  int
	V6        string
	Relay     string
	Tailscale string
	Home      string // empty → REMEDY_HOME / ~/.remedy
}

// HandshakeFields are parsed from a Noise first-message payload.
type HandshakeFields struct {
	Secret   []byte
	Name     string
	DeviceID string
}

func wipePendingLocked() {
	if pendingPair == nil {
		return
	}
	for i := range pendingPair.secret {
		pendingPair.secret[i] = 0
	}
	pendingPair.used = true
	pendingPair = nil
}

// ResetPairStateForTest clears the in-process pending QR (tests only).
func ResetPairStateForTest() {
	pairMu.Lock()
	defer pairMu.Unlock()
	wipePendingLocked()
}

// PendingSecretForTest returns a copy of the live pair secret, or nil.
func PendingSecretForTest() []byte {
	pairMu.Lock()
	defer pairMu.Unlock()
	if pendingPair == nil || pendingPair.used {
		return nil
	}
	out := make([]byte, len(pendingPair.secret))
	copy(out, pendingPair.secret)
	return out
}

// B64u encodes URL-safe base64 without padding.
func B64u(data []byte) string {
	return strings.TrimRight(base64.URLEncoding.EncodeToString(data), "=")
}

// B64uDecode decodes URL-safe base64 with optional missing padding.
func B64uDecode(text string) ([]byte, error) {
	raw := strings.TrimSpace(text)
	if pad := len(raw) % 4; pad != 0 {
		raw += strings.Repeat("=", 4-pad)
	}
	return base64.URLEncoding.DecodeString(raw)
}

// StartPair mints a 60s one-use pair QR. Off-loopback callers get a permission error.
func StartPair(opts PairStartOpts) (string, error) {
	if !opts.Loopback {
		return "", fmt.Errorf("pair start is loopback-only")
	}
	host := strings.TrimSpace(opts.BindHost)
	port := opts.BindPort
	if port == 0 {
		port = DefaultBindPort
	}
	if host == "" {
		return "", fmt.Errorf("bind_host required")
	}
	if port <= 0 || port > 65535 {
		return "", fmt.Errorf("bind_port out of range")
	}
	kp, err := LoadOrCreateHostKeyPair(opts.Home)
	if err != nil {
		return "", err
	}
	secret := make([]byte, PairSecretLen)
	if _, err := rand.Read(secret); err != nil {
		return "", err
	}
	exp := nowFunc().Add(PairTTL).Unix()

	pairMu.Lock()
	wipePendingLocked()
	pendingPair = &pending{secret: secret, exp: time.Unix(exp, 0), used: false}
	pairMu.Unlock()

	lines := []string{
		QRVersion,
		"hp=" + B64u(kp.Public),
		"ps=" + B64u(secret),
		fmt.Sprintf("lan=%s:%d", host, port),
	}
	if v6 := strings.TrimSpace(opts.V6); v6 != "" {
		lines = append(lines, "v6="+v6)
	}
	if relay := strings.TrimSpace(opts.Relay); relay != "" {
		rh, rp, err := ParseRelayEndpoint(relay)
		if err != nil {
			ResetPairStateForTest()
			return "", err
		}
		lines = append(lines, fmt.Sprintf("relay=%s:%d", rh, rp))
	}
	if ts := strings.TrimSpace(opts.Tailscale); ts != "" {
		if IsChosenIPv4(ts) {
			lines = append(lines, fmt.Sprintf("ts=%s:%d", ts, port))
		}
	}
	lines = append(lines, fmt.Sprintf("exp=%d", exp))
	text := strings.Join(lines, "\n")
	lowered := strings.ToLower(text)
	if strings.Contains(lowered, "local_api_token") || strings.Contains(lowered, "bearer ") {
		ResetPairStateForTest()
		return "", fmt.Errorf("refusing to emit a QR that mentions local auth")
	}
	return text, nil
}

// ParsePairSecret decodes the ps= field from QR text.
func ParsePairSecret(qrText string) ([]byte, error) {
	for _, line := range strings.Split(qrText, "\n") {
		if strings.HasPrefix(line, "ps=") {
			return B64uDecode(strings.TrimSpace(line[3:]))
		}
	}
	return nil, fmt.Errorf("QR missing ps")
}

func deviceIDFor(public []byte) string {
	sum := sha256.Sum256(public)
	return hex.EncodeToString(sum[:])[:32]
}

// CompletePair consumes the one-use secret and persists the device. Fail closed.
func CompletePair(secret []byte, devicePub []byte, name, home string) (string, error) {
	presented := secret
	if len(devicePub) != DHLen {
		return "", fmt.Errorf("device public key must be 32 bytes")
	}
	label := strings.TrimSpace(name)
	if label == "" {
		label = "phone"
	}
	if len(label) > 80 {
		label = label[:80]
	}

	pairMu.Lock()
	defer pairMu.Unlock()
	p := pendingPair
	if p == nil || p.used {
		return "", fmt.Errorf("reused")
	}
	if nowFunc().After(p.exp) {
		wipePendingLocked()
		return "", fmt.Errorf("expired")
	}
	if len(presented) != len(p.secret) || subtle.ConstantTimeCompare(presented, p.secret) != 1 {
		return "", fmt.Errorf("invalid")
	}

	existing, err := FindDeviceByPublic(hex.EncodeToString(devicePub), home)
	if err != nil {
		return "", err
	}
	if existing != nil && !existing.Revoked {
		existing.Name = label
		existing.PairedAt = float64(nowFunc().UnixNano()) / 1e9
		if _, err := SaveDevice(*existing, home); err != nil {
			return "", err
		}
		wipePendingLocked()
		return existing.ID, nil
	}
	n, err := ActiveDeviceCount(home)
	if err != nil {
		return "", err
	}
	if n >= MaxDevices {
		wipePendingLocked()
		return "", fmt.Errorf("device limit")
	}
	deviceID := deviceIDFor(devicePub)
	rec := Device{
		ID:        deviceID,
		Name:      label,
		PublicHex: hex.EncodeToString(devicePub),
		PairedAt:  float64(nowFunc().UnixNano()) / 1e9,
		Revoked:   false,
	}
	if _, err := SaveDevice(rec, home); err != nil {
		return "", err
	}
	wipePendingLocked()
	_ = AppendAudit("pair", home, map[string]string{"device_id": deviceID, "name": label})
	return deviceID, nil
}

// PairPayload builds the initiator handshake payload for an unpaired phone.
func PairPayload(secret []byte, name string) []byte {
	label := []byte(strings.TrimSpace(name))
	if len(label) == 0 {
		label = []byte("phone")
	}
	if len(label) > 80 {
		label = label[:80]
	}
	out := make([]byte, 0, 5+len(secret)+1+len(label))
	out = append(out, []byte("pair\x00")...)
	out = append(out, secret...)
	out = append(out, 0)
	out = append(out, label...)
	return out
}

// HelloPayload builds a reconnect handshake payload.
func HelloPayload(deviceID string) []byte {
	return append([]byte("hello\x00"), []byte(deviceID)...)
}

// ParseHandshakePayload returns ("pair"|"hello", fields). Fail closed on garbage.
//
// Phone (Android) first-message payload is the raw 32-byte pair secret.
// Later sessions may send pair\0secret\0name or hello\0id.
func ParseHandshakePayload(payload []byte) (kind string, fields HandshakeFields, err error) {
	data := payload
	if len(data) == PairSecretLen {
		sec := make([]byte, PairSecretLen)
		copy(sec, data)
		return "pair", HandshakeFields{Secret: sec, Name: "phone"}, nil
	}
	if strings.HasPrefix(string(data), "pair\x00") {
		rest := data[5:]
		if len(rest) < PairSecretLen+1 || rest[PairSecretLen] != 0 {
			return "", HandshakeFields{}, fmt.Errorf("invalid pair payload")
		}
		sec := make([]byte, PairSecretLen)
		copy(sec, rest[:PairSecretLen])
		name := string(rest[PairSecretLen+1:])
		return "pair", HandshakeFields{Secret: sec, Name: name}, nil
	}
	if strings.HasPrefix(string(data), "hello\x00") {
		deviceID := strings.TrimSpace(string(data[6:]))
		if deviceID == "" {
			return "", HandshakeFields{}, fmt.Errorf("invalid hello payload")
		}
		return "hello", HandshakeFields{DeviceID: deviceID}, nil
	}
	return "", HandshakeFields{}, fmt.Errorf("unknown handshake payload")
}
