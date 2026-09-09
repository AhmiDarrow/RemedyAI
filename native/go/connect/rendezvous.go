package connect

import (
	"encoding"
	"encoding/binary"
	"encoding/hex"
	"fmt"
	"net"
	"net/url"
	"strconv"
	"strings"
	"time"

	"golang.org/x/crypto/blake2s"
)

const (
	// SessionIDLen is the 16-byte rendezvous id length (matches Python SESSION_ID_LEN).
	SessionIDLen = 16

	// DefaultRelayPort is used when a relay URL omits an explicit port.
	DefaultRelayPort = 7402

	// RDVBucketSeconds rotates public-broker rendezvous ids.
	//
	// Public MQTT brokers accept wildcard subscribers, so a stable id per
	// device pair lets anyone watching the broker enumerate live machines and
	// keep spamming the same topic. Mixing a coarse time bucket into the
	// derivation means a harvested id stops naming anything after an hour.
	RDVBucketSeconds = 3600

	// RDVBucketGrace is how far either side of a bucket boundary the PC also
	// holds the neighbouring id, so a phone with a slightly different clock
	// still meets it.
	RDVBucketGrace = 5 * time.Minute
)

var (
	pairPrefix = []byte("remedy-connect/1|pair|")
	devPrefix  = []byte("remedy-connect/1|dev|")
	rdvPrefix  = []byte("remedy-connect/1|rdv|")
)

// SessionIDPair returns the 16-byte rendezvous id for the 60s pair window.
func SessionIDPair(hostPub, pairSecret []byte) ([]byte, error) {
	if len(hostPub) != DHLen || len(pairSecret) != DHLen {
		return nil, fmt.Errorf("host pub and pair secret must be 32 bytes")
	}
	msg := make([]byte, 0, len(pairPrefix)+len(hostPub)+1+len(pairSecret))
	msg = append(msg, pairPrefix...)
	msg = append(msg, hostPub...)
	msg = append(msg, '|')
	msg = append(msg, pairSecret...)
	return blake2s16(msg)
}

// SessionIDDevice returns the 16-byte rendezvous id for a paired device.
func SessionIDDevice(hostPub, devicePub []byte) ([]byte, error) {
	if len(hostPub) != DHLen || len(devicePub) != DHLen {
		return nil, fmt.Errorf("host and device pubs must be 32 bytes")
	}
	msg := make([]byte, 0, len(devPrefix)+len(hostPub)+1+len(devicePub))
	msg = append(msg, devPrefix...)
	msg = append(msg, hostPub...)
	msg = append(msg, '|')
	msg = append(msg, devicePub...)
	return blake2s16(msg)
}

// RDVBucket returns the rendezvous rotation bucket covering t.
func RDVBucket(t time.Time) int64 {
	return t.Unix() / RDVBucketSeconds
}

// RDVBucketsAt returns the buckets the PC should hold at t: the current one,
// plus the neighbour it is within RDVBucketGrace of, so a clock a few minutes
// off on either side still lands on an id the PC is listening to.
func RDVBucketsAt(t time.Time) []int64 {
	cur := RDVBucket(t)
	out := []int64{cur}
	offset := t.Unix() - cur*RDVBucketSeconds
	grace := int64(RDVBucketGrace / time.Second)
	if offset < grace {
		out = append(out, cur-1)
	}
	if int64(RDVBucketSeconds)-offset <= grace {
		out = append(out, cur+1)
	}
	return out
}

// SessionIDDeviceRDV returns the rotating 16-byte public-broker rendezvous id
// for a paired device in the given time bucket. This is deliberately not the
// relay id: the relay is a chosen host, the public brokers are not.
func SessionIDDeviceRDV(hostPub, devicePub []byte, bucket int64) ([]byte, error) {
	if len(hostPub) != DHLen || len(devicePub) != DHLen {
		return nil, fmt.Errorf("host and device pubs must be 32 bytes")
	}
	stamp := strconv.FormatInt(bucket, 10)
	msg := make([]byte, 0, len(rdvPrefix)+len(hostPub)+len(devicePub)+2+len(stamp))
	msg = append(msg, rdvPrefix...)
	msg = append(msg, hostPub...)
	msg = append(msg, '|')
	msg = append(msg, devicePub...)
	msg = append(msg, '|')
	msg = append(msg, stamp...)
	return blake2s16(msg)
}

// RendezvousSIDsRDV returns the ids the public-broker supervisor should hold
// at t: the live pair-window id (already short-lived) plus the rotating id of
// every non-revoked paired device. The supervisor re-reads this list on its
// tick, so a bucket rollover retires the old topic on its own.
func RendezvousSIDsRDV(home string, t time.Time) ([][]byte, error) {
	out := make([][]byte, 0, MaxDevices*2+1)
	if sid, err := PendingPairRendezvous(home); err != nil {
		return nil, err
	} else if len(sid) == SessionIDLen {
		out = append(out, sid)
	}
	kp, err := LoadOrCreateHostKeyPair(home)
	if err != nil {
		return out, nil
	}
	list, err := ListDevices(home, false)
	if err != nil {
		return out, nil
	}
	buckets := RDVBucketsAt(t)
	for _, rec := range list {
		pub, err := hex.DecodeString(strings.TrimSpace(rec.PublicHex))
		if err != nil || len(pub) != DHLen {
			continue
		}
		for _, bucket := range buckets {
			sid, err := SessionIDDeviceRDV(kp.Public, pub, bucket)
			if err != nil {
				continue
			}
			out = append(out, sid)
		}
	}
	return out, nil
}

// RendezvousSIDs returns the active 16-byte session ids the PC should hold on
// relay and public rendezvous: the live pair-window id (if any) plus one per
// non-revoked paired device. Fail-soft on a bad device record.
func RendezvousSIDs(home string) ([][]byte, error) {
	out := make([][]byte, 0, MaxDevices+1)
	if sid, err := PendingPairRendezvous(home); err != nil {
		return nil, err
	} else if len(sid) == SessionIDLen {
		out = append(out, sid)
	}
	kp, err := LoadOrCreateHostKeyPair(home)
	if err != nil {
		return out, nil
	}
	list, err := ListDevices(home, false)
	if err != nil {
		return out, nil
	}
	for _, rec := range list {
		hx := strings.TrimSpace(rec.PublicHex)
		if hx == "" {
			continue
		}
		pub, err := hex.DecodeString(hx)
		if err != nil || len(pub) != DHLen {
			continue
		}
		sid, err := SessionIDDevice(kp.Public, pub)
		if err != nil {
			continue
		}
		out = append(out, sid)
	}
	return out, nil
}

// blake2s16 is unkeyed BLAKE2s with digest_size=16 (matches Python hashlib).
// x/crypto only exports keyed New128, so we reparameterize a New256 state.
func blake2s16(msg []byte) ([]byte, error) {
	h, err := blake2s.New256(nil)
	if err != nil {
		return nil, err
	}
	marshaler, ok := h.(encoding.BinaryMarshaler)
	if !ok {
		return nil, fmt.Errorf("blake2s: hash does not marshal")
	}
	state, err := marshaler.MarshalBinary()
	if err != nil {
		return nil, err
	}
	// magic(3) + h[8]uint32 + c[2]uint32 + size(1) + block(64) + offset(1)
	// x/crypto marshals h/c as big-endian.
	const magicLen = 3
	h0 := binary.BigEndian.Uint32(state[magicLen:])
	h0 ^= uint32(blake2s.Size) ^ uint32(SessionIDLen)
	binary.BigEndian.PutUint32(state[magicLen:], h0)
	sizeOff := magicLen + 8*4 + 2*4
	state[sizeOff] = byte(SessionIDLen)
	unmarshaler, ok := h.(encoding.BinaryUnmarshaler)
	if !ok {
		return nil, fmt.Errorf("blake2s: hash does not unmarshal")
	}
	if err := unmarshaler.UnmarshalBinary(state); err != nil {
		return nil, err
	}
	if _, err := h.Write(msg); err != nil {
		return nil, err
	}
	sum := h.Sum(nil)
	if len(sum) != SessionIDLen {
		return nil, fmt.Errorf("blake2s: unexpected digest length %d", len(sum))
	}
	return sum, nil
}

// ParseRelayEndpoint parses host:port or tcp://host:port.
// Fail closed: no credentials, query, fragment, HTTP, or wildcard binds.
func ParseRelayEndpoint(rawURL string) (host string, port int, err error) {
	raw := strings.TrimSpace(rawURL)
	if raw == "" {
		return "", 0, fmt.Errorf("relay URL is empty")
	}
	low := strings.ToLower(raw)
	if strings.Contains(low, "local_api_token") || strings.Contains(low, "bearer ") || strings.Contains(low, "authorization=") {
		return "", 0, fmt.Errorf("relay URL must not carry secrets")
	}
	if strings.Contains(raw, "://") {
		parsed, err := url.Parse(raw)
		if err != nil {
			return "", 0, fmt.Errorf("relay URL is invalid")
		}
		scheme := strings.ToLower(parsed.Scheme)
		if scheme == "http" || scheme == "https" {
			return "", 0, fmt.Errorf("relay is a TCP splice, not HTTP")
		}
		if scheme != "" && scheme != "tcp" && scheme != "relay" {
			return "", 0, fmt.Errorf("unsupported relay scheme %q", parsed.Scheme)
		}
		if parsed.User != nil {
			return "", 0, fmt.Errorf("relay URL must not contain credentials")
		}
		if parsed.RawQuery != "" || parsed.Fragment != "" {
			return "", 0, fmt.Errorf("relay URL must not contain a query")
		}
		host = strings.TrimSpace(parsed.Hostname())
		if parsed.Port() != "" {
			port, err = strconv.Atoi(parsed.Port())
			if err != nil {
				return "", 0, fmt.Errorf("relay port out of range")
			}
		} else {
			port = DefaultRelayPort
		}
	} else {
		host, port, err = splitHostPort(raw)
		if err != nil {
			return "", 0, err
		}
	}
	host = strings.Trim(host, "[]")
	if host == "" {
		return "", 0, fmt.Errorf("relay host is empty")
	}
	if IsWildcardBind(host) {
		return "", 0, fmt.Errorf("relay must not be a wildcard bind")
	}
	if port <= 0 || port > 65535 {
		return "", 0, fmt.Errorf("relay port out of range")
	}
	if ip := net.ParseIP(host); ip != nil {
		if ip.IsUnspecified() || ip.IsMulticast() {
			return "", 0, fmt.Errorf("relay address is not a unicast host")
		}
	} else if host == "" || strings.ContainsAny(host, "/ \\") || strings.Contains(host, "..") {
		return "", 0, fmt.Errorf("relay host is not a hostname")
	}
	return host, port, nil
}

func splitHostPort(raw string) (string, int, error) {
	text := strings.TrimSpace(raw)
	if text == "" {
		return "", 0, fmt.Errorf("relay host is empty")
	}
	if strings.HasPrefix(text, "[") {
		end := strings.Index(text, "]")
		if end < 0 {
			return "", 0, fmt.Errorf("relay IPv6 address is missing ']'")
		}
		host := text[1:end]
		rest := text[end+1:]
		if !strings.HasPrefix(rest, ":") {
			return host, DefaultRelayPort, nil
		}
		portStr := rest[1:]
		if portStr == "" {
			return host, DefaultRelayPort, nil
		}
		port, err := strconv.Atoi(portStr)
		if err != nil {
			return "", 0, fmt.Errorf("relay port out of range")
		}
		return host, port, nil
	}
	if strings.Count(text, ":") == 1 {
		host, portStr, _ := strings.Cut(text, ":")
		if portStr == "" {
			return host, DefaultRelayPort, nil
		}
		port, err := strconv.Atoi(portStr)
		if err != nil {
			return "", 0, fmt.Errorf("relay port out of range")
		}
		return host, port, nil
	}
	if strings.Count(text, ":") > 1 {
		// bare IPv6 without port
		return text, DefaultRelayPort, nil
	}
	return text, DefaultRelayPort, nil
}
