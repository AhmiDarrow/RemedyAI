package connect

import (
	"encoding/binary"
	"errors"
	"net"
	"testing"
	"time"
)

func TestEncodeBindingRequestHasMagicCookie(t *testing.T) {
	txid := make([]byte, 12)
	for i := range txid {
		txid[i] = byte(i)
	}
	pkt, err := EncodeBindingRequest(txid)
	if err != nil {
		t.Fatal(err)
	}
	if len(pkt) != 20 {
		t.Fatalf("len=%d", len(pkt))
	}
	cookie := binary.BigEndian.Uint32(pkt[4:8])
	length := binary.BigEndian.Uint16(pkt[2:4])
	if cookie != MagicCookie {
		t.Fatalf("cookie=%#x", cookie)
	}
	if length != 0 {
		t.Fatalf("length=%d", length)
	}
	if string(pkt[8:]) != string(txid) {
		t.Fatalf("txid mismatch")
	}
}

func TestParseMappedAddressCraftedXorIPv4(t *testing.T) {
	txid := make([]byte, 12)
	for i := range txid {
		txid[i] = byte(i)
	}
	ip := net.ParseIP("203.0.113.10").To4()
	port := 3478
	xport := port ^ int(MagicCookie>>16)
	xip := xorIPv4(ip)
	attr := make([]byte, 4+8)
	binary.BigEndian.PutUint16(attr[0:2], AttrXorMappedAddress)
	binary.BigEndian.PutUint16(attr[2:4], 8)
	attr[4] = 0
	attr[5] = FamilyIPv4
	binary.BigEndian.PutUint16(attr[6:8], uint16(xport))
	copy(attr[8:], xip)
	header := make([]byte, stunHeaderLen)
	binary.BigEndian.PutUint16(header[0:2], BindingSuccess)
	binary.BigEndian.PutUint16(header[2:4], uint16(len(attr)))
	binary.BigEndian.PutUint32(header[4:8], MagicCookie)
	copy(header[8:], txid)
	pkt := append(header, attr...)

	gotHost, gotPort, err := ParseMappedAddress(pkt)
	if err != nil {
		t.Fatal(err)
	}
	if gotHost != "203.0.113.10" || gotPort != port {
		t.Fatalf("got %s:%d", gotHost, gotPort)
	}
}

func TestParseMappedAddressCraftedPlainIPv4(t *testing.T) {
	txid := make([]byte, 12)
	for i := range txid {
		txid[i] = 0xab
	}
	ip := net.ParseIP("192.0.2.55").To4()
	port := 12345
	attr := make([]byte, 4+8)
	binary.BigEndian.PutUint16(attr[0:2], AttrMappedAddress)
	binary.BigEndian.PutUint16(attr[2:4], 8)
	attr[4] = 0
	attr[5] = FamilyIPv4
	binary.BigEndian.PutUint16(attr[6:8], uint16(port))
	copy(attr[8:], ip)
	header := make([]byte, stunHeaderLen)
	binary.BigEndian.PutUint16(header[0:2], BindingSuccess)
	binary.BigEndian.PutUint16(header[2:4], uint16(len(attr)))
	binary.BigEndian.PutUint32(header[4:8], MagicCookie)
	copy(header[8:], txid)

	gotHost, gotPort, err := ParseMappedAddress(append(header, attr...))
	if err != nil {
		t.Fatal(err)
	}
	if gotHost != "192.0.2.55" || gotPort != port {
		t.Fatalf("got %s:%d", gotHost, gotPort)
	}
}

func TestParseMappedAddressXorIPv6(t *testing.T) {
	txid := make([]byte, 12)
	for i := range txid {
		txid[i] = byte(i)
	}
	const (
		ip   = "2001:db8::1"
		port = 5353
	)
	pkt, err := EncodeBindingSuccess(ip, port, txid, true)
	if err != nil {
		t.Fatal(err)
	}
	gotHost, gotPort, err := ParseMappedAddress(pkt)
	if err != nil {
		t.Fatal(err)
	}
	if net.ParseIP(gotHost).String() != net.ParseIP(ip).String() {
		t.Fatalf("host got %q want %q", gotHost, ip)
	}
	if gotPort != port {
		t.Fatalf("port=%d", gotPort)
	}
}

func TestParseMappedAddressGarbageFailsClosed(t *testing.T) {
	cases := [][]byte{
		{},
		make([]byte, 20),
		[]byte("not-stun"),
	}
	for _, pkt := range cases {
		_, _, err := ParseMappedAddress(pkt)
		if !errors.Is(err, ErrSTUN) {
			t.Fatalf("pkt %q: want ErrSTUN, got %v", pkt, err)
		}
	}
}

func TestIPv6Helpers(t *testing.T) {
	if !IsIPv6Literal("2001:db8::1") {
		t.Fatal("expected IPv6 literal")
	}
	if !IsIPv6Literal("[::1]") {
		t.Fatal("expected bracketed IPv6")
	}
	if IsIPv6Literal("127.0.0.1") {
		t.Fatal("IPv4 must not be IPv6 literal")
	}
	if WrapIPv6Host("2001:db8::1") != "[2001:db8::1]" {
		t.Fatalf("wrap=%q", WrapIPv6Host("2001:db8::1"))
	}
	if WrapIPv6Host("127.0.0.1") != "127.0.0.1" {
		t.Fatalf("wrap ipv4=%q", WrapIPv6Host("127.0.0.1"))
	}
	if FamilyForHost("::1") == FamilyForHost("127.0.0.1") {
		t.Fatal("families must differ")
	}
	if FamilyForHost("::1") != "udp6" || FamilyForHost("127.0.0.1") != "udp4" {
		t.Fatalf("families=%s/%s", FamilyForHost("::1"), FamilyForHost("127.0.0.1"))
	}
}

type fakePacketConn struct {
	payload []byte
	addr    net.Addr
	n       int
	closed  bool
}

func (f *fakePacketConn) ReadFrom([]byte) (int, net.Addr, error) { return 0, nil, nil }
func (f *fakePacketConn) WriteTo(p []byte, addr net.Addr) (int, error) {
	f.payload = append([]byte(nil), p...)
	f.addr = addr
	if f.n != 0 {
		return f.n, nil
	}
	return len(p), nil
}
func (f *fakePacketConn) Close() error                     { f.closed = true; return nil }
func (f *fakePacketConn) LocalAddr() net.Addr              { return nil }
func (f *fakePacketConn) SetDeadline(time.Time) error      { return nil }
func (f *fakePacketConn) SetReadDeadline(time.Time) error  { return nil }
func (f *fakePacketConn) SetWriteDeadline(time.Time) error { return nil }

func TestUDPPunchOnceSendsOneByte(t *testing.T) {
	sock := &fakePacketConn{n: 1}
	n, err := UDPPunchOnce("127.0.0.1", 40000, "203.0.113.9", 40001, sock)
	if err != nil {
		t.Fatal(err)
	}
	if n != 1 {
		t.Fatalf("n=%d", n)
	}
	if string(sock.payload) != "\x00" {
		t.Fatalf("payload=%x", sock.payload)
	}
	ua, ok := sock.addr.(*net.UDPAddr)
	if !ok || ua.Port != 40001 || !ua.IP.Equal(net.ParseIP("203.0.113.9")) {
		t.Fatalf("dest=%v", sock.addr)
	}
	if sock.closed {
		t.Fatal("injected sock must not be closed")
	}
}

func TestUDPPunchOnceIPv6Dest(t *testing.T) {
	sock := &fakePacketConn{n: 1}
	_, err := UDPPunchOnce("::1", 40000, "2001:db8::9", 40001, sock)
	if err != nil {
		t.Fatal(err)
	}
	ua, ok := sock.addr.(*net.UDPAddr)
	if !ok || ua.Port != 40001 || !ua.IP.Equal(net.ParseIP("2001:db8::9")) {
		t.Fatalf("dest=%v", sock.addr)
	}
}

func TestEncodeBindingRequestRejectsBadTxID(t *testing.T) {
	_, err := EncodeBindingRequest([]byte{1, 2, 3})
	if !errors.Is(err, ErrSTUN) {
		t.Fatalf("got %v", err)
	}
}

func TestParseMappedAddressPrefersXor(t *testing.T) {
	txid := make([]byte, 12)
	for i := range txid {
		txid[i] = byte(i)
	}
	plain, err := EncodeXorMappedAddress("192.0.2.1", 1111, txid, false)
	if err != nil {
		t.Fatal(err)
	}
	xored, err := EncodeXorMappedAddress("203.0.113.10", 3478, txid, true)
	if err != nil {
		t.Fatal(err)
	}
	attrs := append(plain, xored...)
	header := make([]byte, stunHeaderLen)
	binary.BigEndian.PutUint16(header[0:2], BindingSuccess)
	binary.BigEndian.PutUint16(header[2:4], uint16(len(attrs)))
	binary.BigEndian.PutUint32(header[4:8], MagicCookie)
	copy(header[8:], txid)
	host, port, err := ParseMappedAddress(append(header, attrs...))
	if err != nil {
		t.Fatal(err)
	}
	if host != "203.0.113.10" || port != 3478 {
		t.Fatalf("got %s:%d (want xor)", host, port)
	}
}

func TestSockaddrStripsBrackets(t *testing.T) {
	a := Sockaddr("[2001:db8::1]", 5353)
	if a.Port != 5353 || !a.IP.Equal(net.ParseIP("2001:db8::1")) {
		t.Fatalf("%v", a)
	}
}
