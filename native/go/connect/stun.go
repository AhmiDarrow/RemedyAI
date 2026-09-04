package connect

import (
	"crypto/rand"
	"encoding/binary"
	"errors"
	"fmt"
	"net"
	"strings"
)

// Public STUN (RFC 5389) Binding encode/decode + a one-byte UDP punch helper.
//
// Simultaneous Noise-over-UDP is out of scope (the Connect pipe is TCP);
// UDPPunchOnce only sends a 1-byte probe.

const (
	// MagicCookie is the RFC 5389 STUN magic cookie.
	MagicCookie = 0x2112A442

	// BindingRequest is message type 0x0001.
	BindingRequest = 0x0001
	// BindingSuccess is message type 0x0101.
	BindingSuccess = 0x0101

	// AttrMappedAddress is attribute type 0x0001.
	AttrMappedAddress = 0x0001
	// AttrXorMappedAddress is attribute type 0x0020.
	AttrXorMappedAddress = 0x0020

	// FamilyIPv4 / FamilyIPv6 are STUN address families.
	FamilyIPv4 = 0x01
	FamilyIPv6 = 0x02

	stunHeaderLen = 20
	stunTxIDLen   = 12
)

// ErrSTUN is a STUN framing / address parse failure (fail closed).
var ErrSTUN = errors.New("stun error")

var magicCookieBytes = [4]byte{0x21, 0x12, 0xa4, 0x42}

// IsIPv6Literal reports whether host is an IPv6 address (optional brackets).
// Textual IPv4-mapped forms (e.g. ::ffff:127.0.0.1) count as IPv6, matching
// Python ipaddress.ip_address(...).version == 6.
func IsIPv6Literal(host string) bool {
	raw := strings.Trim(strings.TrimSpace(host), "[]")
	if raw == "" {
		return false
	}
	ip := net.ParseIP(raw)
	if ip == nil {
		return false
	}
	return strings.Contains(raw, ":")
}

// WrapIPv6Host brackets an IPv6 literal for host:port pairing; leaves IPv4 alone.
func WrapIPv6Host(host string) string {
	h := strings.TrimSpace(host)
	if h == "" {
		return h
	}
	if strings.HasPrefix(h, "[") && strings.HasSuffix(h, "]") {
		return h
	}
	if IsIPv6Literal(h) {
		return "[" + h + "]"
	}
	return h
}

// FamilyForHost returns "udp6" for IPv6 literals, else "udp4".
func FamilyForHost(host string) string {
	if IsIPv6Literal(host) {
		return "udp6"
	}
	return "udp4"
}

// Sockaddr builds a UDP address for bind/sendto (brackets stripped).
// Non-literal hosts yield a UDPAddr with a nil IP (caller/resolvers handle that).
func Sockaddr(host string, port int) *net.UDPAddr {
	h := strings.Trim(strings.TrimSpace(host), "[]")
	return &net.UDPAddr{IP: net.ParseIP(h), Port: port}
}

// EncodeBindingRequest builds an RFC 5389 Binding request (20-byte header, no attributes).
// When transactionID is nil, a fresh 12-byte id is generated.
func EncodeBindingRequest(transactionID []byte) ([]byte, error) {
	txid, err := stunTxID(transactionID)
	if err != nil {
		return nil, err
	}
	out := make([]byte, stunHeaderLen)
	binary.BigEndian.PutUint16(out[0:2], BindingRequest)
	binary.BigEndian.PutUint16(out[2:4], 0)
	binary.BigEndian.PutUint32(out[4:8], MagicCookie)
	copy(out[8:], txid)
	return out, nil
}

func stunTxID(transactionID []byte) ([]byte, error) {
	if transactionID == nil {
		txid := make([]byte, stunTxIDLen)
		if _, err := rand.Read(txid); err != nil {
			return nil, fmt.Errorf("%w: random txid: %v", ErrSTUN, err)
		}
		return txid, nil
	}
	if len(transactionID) != stunTxIDLen {
		return nil, fmt.Errorf("%w: STUN transaction id must be 12 bytes", ErrSTUN)
	}
	out := make([]byte, stunTxIDLen)
	copy(out, transactionID)
	return out, nil
}

func xorPort(port int) int {
	return port ^ int(MagicCookie>>16)
}

func xorIPv4(packed []byte) []byte {
	out := make([]byte, 4)
	for i := 0; i < 4; i++ {
		out[i] = packed[i] ^ magicCookieBytes[i]
	}
	return out
}

func xorIPv6(packed, txid []byte) []byte {
	out := make([]byte, 16)
	for i := 0; i < 4; i++ {
		out[i] = packed[i] ^ magicCookieBytes[i]
	}
	for i := 0; i < 12; i++ {
		out[4+i] = packed[4+i] ^ txid[i]
	}
	return out
}

// EncodeXorMappedAddress builds a MAPPED-ADDRESS or XOR-MAPPED-ADDRESS
// attribute (header + body + padding).
func EncodeXorMappedAddress(host string, port int, transactionID []byte, xor bool) ([]byte, error) {
	h := strings.Trim(strings.TrimSpace(host), "[]")
	ip := net.ParseIP(h)
	if ip == nil {
		return nil, fmt.Errorf("%w: invalid IP %q", ErrSTUN, host)
	}
	if port < 0 || port > 65535 {
		return nil, fmt.Errorf("%w: invalid port %d", ErrSTUN, port)
	}
	if xor && len(transactionID) != stunTxIDLen {
		return nil, fmt.Errorf("%w: STUN transaction id must be 12 bytes", ErrSTUN)
	}

	var (
		family uint8
		packed []byte
	)
	if v4 := ip.To4(); v4 != nil {
		family = FamilyIPv4
		packed = append([]byte(nil), v4...)
	} else {
		family = FamilyIPv6
		packed = ip.To16()
		if packed == nil {
			return nil, fmt.Errorf("%w: invalid IPv6 %q", ErrSTUN, host)
		}
		packed = append([]byte(nil), packed...)
	}

	xport := uint16(port)
	xaddr := packed
	if xor {
		xport = uint16(xorPort(port))
		if family == FamilyIPv6 {
			xaddr = xorIPv6(packed, transactionID)
		} else {
			xaddr = xorIPv4(packed)
		}
	}

	body := make([]byte, 4+len(xaddr))
	body[0] = 0
	body[1] = family
	binary.BigEndian.PutUint16(body[2:4], xport)
	copy(body[4:], xaddr)

	atype := uint16(AttrMappedAddress)
	if xor {
		atype = AttrXorMappedAddress
	}
	pad := (4 - (len(body) % 4)) % 4
	out := make([]byte, 4+len(body)+pad)
	binary.BigEndian.PutUint16(out[0:2], atype)
	binary.BigEndian.PutUint16(out[2:4], uint16(len(body)))
	copy(out[4:], body)
	return out, nil
}

// EncodeBindingSuccess crafts a Binding success with a (XOR-)MAPPED-ADDRESS.
// When transactionID is nil, a fresh 12-byte id is generated.
func EncodeBindingSuccess(host string, port int, transactionID []byte, xor bool) ([]byte, error) {
	txid, err := stunTxID(transactionID)
	if err != nil {
		return nil, err
	}
	attr, err := EncodeXorMappedAddress(host, port, txid, xor)
	if err != nil {
		return nil, err
	}
	out := make([]byte, stunHeaderLen+len(attr))
	binary.BigEndian.PutUint16(out[0:2], BindingSuccess)
	binary.BigEndian.PutUint16(out[2:4], uint16(len(attr)))
	binary.BigEndian.PutUint32(out[4:8], MagicCookie)
	copy(out[8:stunHeaderLen], txid)
	copy(out[stunHeaderLen:], attr)
	return out, nil
}

func parseOneAddress(attrType uint16, value, txid []byte) (string, int, error) {
	if len(value) < 4 {
		return "", 0, fmt.Errorf("%w: short address attribute", ErrSTUN)
	}
	family := value[1]
	port := int(binary.BigEndian.Uint16(value[2:4]))
	xor := attrType == AttrXorMappedAddress
	if xor {
		port = xorPort(port)
	}
	switch family {
	case FamilyIPv4:
		if len(value) < 8 {
			return "", 0, fmt.Errorf("%w: short IPv4 address", ErrSTUN)
		}
		packed := append([]byte(nil), value[4:8]...)
		if xor {
			packed = xorIPv4(packed)
		}
		ip := net.IP(packed)
		if ip.To4() == nil {
			return "", 0, fmt.Errorf("%w: invalid IPv4 address bytes", ErrSTUN)
		}
		return ip.String(), port, nil
	case FamilyIPv6:
		if len(value) < 20 {
			return "", 0, fmt.Errorf("%w: short IPv6 address", ErrSTUN)
		}
		packed := append([]byte(nil), value[4:20]...)
		if xor {
			packed = xorIPv6(packed, txid)
		}
		return net.IP(packed).String(), port, nil
	default:
		return "", 0, fmt.Errorf("%w: unknown address family %#x", ErrSTUN, family)
	}
}

// ParseMappedAddress returns (ip, port) from XOR-MAPPED-ADDRESS or MAPPED-ADDRESS.
// Bad packets fail closed with ErrSTUN (no soft nil).
func ParseMappedAddress(response []byte) (string, int, error) {
	if len(response) < stunHeaderLen {
		return "", 0, fmt.Errorf("%w: short STUN message", ErrSTUN)
	}
	length := int(binary.BigEndian.Uint16(response[2:4]))
	cookie := binary.BigEndian.Uint32(response[4:8])
	if cookie != MagicCookie {
		return "", 0, fmt.Errorf("%w: bad magic cookie", ErrSTUN)
	}
	if len(response) < stunHeaderLen+length {
		return "", 0, fmt.Errorf("%w: truncated STUN attributes", ErrSTUN)
	}
	txid := response[8:stunHeaderLen]
	attrs := response[stunHeaderLen : stunHeaderLen+length]

	var (
		xorHit     string
		xorPortN   int
		haveXor    bool
		mappedHit  string
		mappedPort int
		haveMapped bool
	)

	offset := 0
	for offset+4 <= len(attrs) {
		atype := binary.BigEndian.Uint16(attrs[offset : offset+2])
		alen := int(binary.BigEndian.Uint16(attrs[offset+2 : offset+4]))
		offset += 4
		if offset+alen > len(attrs) {
			return "", 0, fmt.Errorf("%w: truncated attribute value", ErrSTUN)
		}
		value := attrs[offset : offset+alen]
		offset += alen
		pad := (4 - (alen % 4)) % 4
		if offset+pad > len(attrs) {
			return "", 0, fmt.Errorf("%w: truncated attribute padding", ErrSTUN)
		}
		offset += pad

		switch atype {
		case AttrXorMappedAddress:
			host, port, err := parseOneAddress(atype, value, txid)
			if err != nil {
				return "", 0, err
			}
			xorHit, xorPortN, haveXor = host, port, true
		case AttrMappedAddress:
			host, port, err := parseOneAddress(atype, value, txid)
			if err != nil {
				return "", 0, err
			}
			mappedHit, mappedPort, haveMapped = host, port, true
		}
	}
	if offset != len(attrs) && offset+4 > len(attrs) && offset < len(attrs) {
		// Trailing bytes that cannot form a full attribute header — fail closed.
		return "", 0, fmt.Errorf("%w: trailing attribute bytes", ErrSTUN)
	}
	if haveXor {
		return xorHit, xorPortN, nil
	}
	if haveMapped {
		return mappedHit, mappedPort, nil
	}
	return "", 0, fmt.Errorf("%w: no mapped address", ErrSTUN)
}

// UDPPunchOnce sends a 1-byte UDP probe from local toward peer.
// When conn is non-nil it is used as-is and not closed (tests inject fakes).
// When conn is nil a datagram socket is created, bound to local, and closed.
func UDPPunchOnce(localHost string, localPort int, peerHost string, peerPort int, conn net.PacketConn) (int, error) {
	payload := []byte{0x00}
	owned := conn == nil
	if conn == nil {
		network := FamilyForHost(localHost)
		c, err := net.ListenUDP(network, Sockaddr(localHost, localPort))
		if err != nil {
			return 0, err
		}
		conn = c
	}
	if owned {
		defer conn.Close()
	}
	return conn.WriteTo(payload, Sockaddr(peerHost, peerPort))
}
