package connect

import (
	"context"
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"net/netip"
	"regexp"
	"strings"
	"sync"
	"time"

	"golang.org/x/crypto/chacha20poly1305"
)

// Relay-free hole punching ("Get through carrier-grade NAT", opt-in).
//
// When the PC is not reachable directly (CGNAT, no router mapping), the phone
// and the PC each learn the public address of a fresh UDP port with STUN and
// swap those addresses over the public MQTT brokers Remedy already uses for
// rendezvous. Both then send at once so each NAT opens a mapping for the
// other, and the phone connects to the PC's iroh endpoint on that port. No
// relay carries any traffic.
//
// The swap is encrypted with a key derived from the PC's Noise host public
// key, which only paired phones (and anyone who saw the pairing QR) know, so
// a broker sees ciphertext, topic names and connection timing — never
// addresses. The QUIC handshake still authenticates the PC's iroh id, and the
// Noise session inside still pins the host key: a forged answer can at worst
// make a punch fail.

const (
	// punchVersion is the offer/answer wire version.
	punchVersion = 1
	// punchMaxSkew bounds replayed offers.
	punchMaxSkew = 2 * time.Minute
	// punchWait is how long the responder waits for the sidecar's port.
	punchWait = 12 * time.Second
	// maxPunchAddrs bounds candidate lists either way.
	maxPunchAddrs = 8
)

var punchNonceRE = regexp.MustCompile(`^[0-9a-f]{16,64}$`)

// PunchOffer is what the phone publishes (decrypted).
type PunchOffer struct {
	V     int      `json:"v"`
	From  string   `json:"from"`  // phone's punch-endpoint iroh id
	Addrs []string `json:"addrs"` // phone's STUN-mapped address first
	Nonce string   `json:"nonce"` // hex; also names the reply topic
	TS    int64    `json:"ts"`    // unix seconds
}

// PunchAnswer is what the PC publishes back (decrypted).
type PunchAnswer struct {
	V     int      `json:"v"`
	Nonce string   `json:"nonce"`
	Addrs []string `json:"addrs,omitempty"`
	Error string   `json:"error,omitempty"`
}

// PunchTopic is the broker topic offers for this PC arrive on.
func PunchTopic(hostPub []byte) string {
	sum := sha256.Sum256(append([]byte("remedy-punch|"), hostPub...))
	return "remedy/punch/" + hex.EncodeToString(sum[:])[:32]
}

// PunchReplyTopic is where the answer to one offer goes.
func PunchReplyTopic(hostPub []byte, nonce string) string {
	return PunchTopic(hostPub) + "/" + nonce
}

func punchKey(hostPub []byte) []byte {
	sum := sha256.Sum256(append([]byte("remedy-punch-key|"), hostPub...))
	return sum[:]
}

// SealPunch encrypts a punch message: nonce(12) || ChaCha20-Poly1305.
func SealPunch(hostPub []byte, v any) ([]byte, error) {
	plain, err := json.Marshal(v)
	if err != nil {
		return nil, err
	}
	aead, err := chacha20poly1305.New(punchKey(hostPub))
	if err != nil {
		return nil, err
	}
	nonce := make([]byte, aead.NonceSize())
	if _, err := rand.Read(nonce); err != nil {
		return nil, err
	}
	return aead.Seal(nonce, nonce, plain, []byte("remedy-punch/1")), nil
}

// OpenPunch decrypts a punch message into v.
func OpenPunch(hostPub, blob []byte, v any) error {
	aead, err := chacha20poly1305.New(punchKey(hostPub))
	if err != nil {
		return err
	}
	if len(blob) < aead.NonceSize()+aead.Overhead() || len(blob) > 4096 {
		return errors.New("punch: bad message size")
	}
	plain, err := aead.Open(nil, blob[:aead.NonceSize()], blob[aead.NonceSize():], []byte("remedy-punch/1"))
	if err != nil {
		return errors.New("punch: cannot decrypt")
	}
	return json.Unmarshal(plain, v)
}

// validPunchAddrs keeps literal ip:port candidates only (no DNS, no wildcards).
func validPunchAddrs(in []string) []string {
	var out []string
	for _, a := range in {
		ap, err := netip.ParseAddrPort(strings.TrimSpace(a))
		if err != nil || ap.Port() == 0 || ap.Addr().IsUnspecified() || ap.Addr().IsMulticast() {
			continue
		}
		out = append(out, ap.String())
		if len(out) == maxPunchAddrs {
			break
		}
	}
	return out
}

// checkOffer validates a decrypted offer.
func checkOffer(o PunchOffer, now time.Time) error {
	if o.V != punchVersion {
		return fmt.Errorf("punch: version %d", o.V)
	}
	if !punchNonceRE.MatchString(o.Nonce) {
		return errors.New("punch: bad nonce")
	}
	if len(o.From) != 64 || !isHex(o.From) {
		return errors.New("punch: bad peer id")
	}
	if d := now.Sub(time.Unix(o.TS, 0)); d > punchMaxSkew || d < -punchMaxSkew {
		return errors.New("punch: stale offer")
	}
	if len(validPunchAddrs(o.Addrs)) == 0 {
		return errors.New("punch: no usable addresses")
	}
	return nil
}

func isHex(s string) bool {
	_, err := hex.DecodeString(s)
	return err == nil
}

// PunchFunc asks the sidecar to open a punch endpoint toward the peer and
// returns the PC's candidate addresses (STUN-mapped first).
type PunchFunc func(ctx context.Context, peerID string, peerAddrs []string) ([]string, error)

// punchResponder answers offers; seen dedupes replays within the skew window.
type punchResponder struct {
	hostPub []byte
	punch   PunchFunc
	mu      sync.Mutex
	seen    map[string]time.Time
}

// handle decrypts one offer and returns the sealed answer and its topic.
// ok=false means stay silent (junk, replay, or not for us).
func (r *punchResponder) handle(ctx context.Context, blob []byte, now time.Time) (topic string, answer []byte, ok bool) {
	var o PunchOffer
	if OpenPunch(r.hostPub, blob, &o) != nil {
		return "", nil, false
	}
	if checkOffer(o, now) != nil {
		return "", nil, false
	}
	r.mu.Lock()
	if r.seen == nil {
		r.seen = map[string]time.Time{}
	}
	for k, t := range r.seen {
		if now.Sub(t) > 2*punchMaxSkew {
			delete(r.seen, k)
		}
	}
	if _, dup := r.seen[o.Nonce]; dup || len(r.seen) > 256 {
		r.mu.Unlock()
		return "", nil, false
	}
	r.seen[o.Nonce] = now
	r.mu.Unlock()

	ans := PunchAnswer{V: punchVersion, Nonce: o.Nonce}
	pctx, cancel := context.WithTimeout(ctx, punchWait)
	addrs, err := r.punch(pctx, o.From, validPunchAddrs(o.Addrs))
	cancel()
	if err != nil {
		ans.Error = "PC could not open a port"
	} else {
		ans.Addrs = validPunchAddrs(addrs)
	}
	sealed, err := SealPunch(r.hostPub, ans)
	if err != nil {
		return "", nil, false
	}
	return PunchReplyTopic(r.hostPub, o.Nonce), sealed, true
}

// RunIrohPunchResponder listens for offers on every broker until ctx ends.
func RunIrohPunchResponder(ctx context.Context, hostPub []byte, brokers []RDVEndpoint, punch PunchFunc) {
	r := &punchResponder{hostPub: hostPub, punch: punch}
	var wg sync.WaitGroup
	for _, b := range brokers {
		wg.Add(1)
		go func(b RDVEndpoint) {
			defer wg.Done()
			runPunchBroker(ctx, b, r)
		}(b)
	}
	wg.Wait()
}

func runPunchBroker(ctx context.Context, b RDVEndpoint, r *punchResponder) {
	backoff := 2 * time.Second
	topic := PunchTopic(r.hostPub)
	for ctx.Err() == nil {
		mqtt, err := NewMqttSession(b.Host, b.Port, 8*time.Second)
		if err == nil {
			cctx, cancel := context.WithTimeout(ctx, 8*time.Second)
			err = mqtt.Connect(cctx)
			cancel()
		}
		if err == nil {
			lctx, lcancel := context.WithCancel(ctx)
			mqtt.OnMessage = func(t string, payload []byte) {
				if t != topic {
					return
				}
				blob := append([]byte(nil), payload...)
				// Publishing waits for PUBACK, which this reader loop
				// delivers: answer off the loop.
				go func() {
					rt, ans, ok := r.handle(lctx, blob, time.Now())
					if !ok {
						return
					}
					pctx, cancel := context.WithTimeout(lctx, 8*time.Second)
					_ = mqtt.Publish(pctx, rt, ans, 1)
					cancel()
				}()
			}
			sctx, cancel := context.WithTimeout(ctx, 8*time.Second)
			err = mqtt.Subscribe(sctx, []string{topic}, 1)
			cancel()
			if err == nil {
				backoff = 2 * time.Second
				// ReaderLoop gives up on a read that outlasts Timeout, and an
				// idle offer topic only sees PINGRESP every PingEveryS. Keep
				// the read window above two pings so a quiet subscription
				// stays up instead of silently dropping offers.
				mqtt.Timeout = 2*PingEveryS + 5*time.Second
				// A blocked read ignores ctx; closing the session is what
				// lets a settings change stop the responder at once.
				go func(m *MqttSession) {
					<-lctx.Done()
					m.Close()
				}(mqtt)
				go mqtt.PingLoop(lctx)
				mqtt.ReaderLoop(lctx)
			}
			lcancel()
		}
		if mqtt != nil {
			mqtt.Close()
		}
		select {
		case <-ctx.Done():
			return
		case <-time.After(backoff):
		}
		if backoff < time.Minute {
			backoff *= 2
		}
	}
}

// PunchBrokerList is the irp= value: the brokers the responder listens on.
func PunchBrokerList(brokers []RDVEndpoint) string {
	parts := make([]string, 0, len(brokers))
	for _, b := range brokers {
		parts = append(parts, fmt.Sprintf("%s:%d", b.Host, b.Port))
	}
	return strings.Join(parts, ";")
}

// DefaultPunchBrokers mirrors the public rendezvous brokers.
func DefaultPunchBrokers() []RDVEndpoint {
	out := make([]RDVEndpoint, 0, len(PublicRDVEndpoints))
	for _, ep := range PublicRDVEndpoints {
		out = append(out, RDVEndpoint{Host: ep.Host, Port: ep.Port})
	}
	return out
}
