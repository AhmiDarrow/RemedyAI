package connect

import (
	"encoding"
	"encoding/binary"
	"fmt"
	"net"
	"net/url"
	"strconv"
	"strings"

	"golang.org/x/crypto/blake2s"
)

const (
	// SessionIDLen is the 16-byte rendezvous id length (matches Python SESSION_ID_LEN).
	SessionIDLen = 16

	// DefaultRelayPort is used when a relay URL omits an explicit port.
	DefaultRelayPort = 7402
)

var (
	pairPrefix = []byte("remedy-connect/1|pair|")
	devPrefix  = []byte("remedy-connect/1|dev|")
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
