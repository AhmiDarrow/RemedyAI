package connect

import (
	"encoding/binary"
	"encoding/hex"
	"errors"
	"fmt"
	"net"
	"strings"
	"sync"
	"time"
	"unicode"

	"golang.org/x/crypto/blake2s"
)

// Same-LAN mDNS advertise for Grove Connect.
//
// Service: _remedy-connect._udp on the RFC 6762 group 224.0.0.251:5353.
// TXT carries a host-pub hash (blake2s-256 of the public key, first 16 hex
// chars). Never the local API, Bearer token, or pair secret.
//
// No extra dependency beyond x/crypto (blake2s). Packets are stdlib UDP.

const (
	// ServiceType is the DNS-SD type for Grove Connect.
	ServiceType = "_remedy-connect._udp"
	// ServiceName is ServiceType under .local.
	ServiceName = ServiceType + ".local"
	// MDNSGroup is the IPv4 mDNS multicast address.
	MDNSGroup = "224.0.0.251"
	// MDNSPort is the mDNS UDP port.
	MDNSPort = 5353

	dnsPTR = 12
	dnsTXT = 16
	dnsA   = 1
	dnsSRV = 33
	dnsIN  = 1

	// QR=1, AA=1 — mDNS announcement response flags.
	mdnsResponseFlags = 0x8400
	mdnsTTL           = 120
	mdnsAnnounceEvery = 1500 * time.Millisecond
	mdnsStopJoin      = 2 * time.Second
)

// ErrMDNS is an mDNS encode / advertise policy failure (fail closed).
var ErrMDNS = errors.New("mdns error")

// HostPubHash is blake2s-256 of the host public key, truncated to 16 hex chars.
func HostPubHash(hostPub []byte) string {
	sum := blake2s.Sum256(hostPub)
	return hex.EncodeToString(sum[:])[:16]
}

func advertiseIPv4(bindHost string) (string, error) {
	host, err := AssertChosenBind(bindHost)
	if err != nil {
		return "", err
	}
	if !IsChosenIPv4(host) {
		return "", fmt.Errorf("%w: mDNS advertise needs a chosen IPv4, not %q", ErrMDNS, bindHost)
	}
	return host, nil
}

// EncodeDNSName encodes length-prefixed DNS labels with no compression.
func EncodeDNSName(name string) ([]byte, error) {
	trimmed := strings.TrimSpace(name)
	trimmed = strings.TrimRight(trimmed, ".")
	if trimmed == "" {
		return []byte{0}, nil
	}
	var out []byte
	for _, label := range strings.Split(trimmed, ".") {
		if len(label) > 63 {
			return nil, fmt.Errorf("%w: DNS label too long: %q", ErrMDNS, label)
		}
		for _, r := range label {
			if r > unicode.MaxASCII {
				return nil, fmt.Errorf("%w: DNS label not ASCII: %q", ErrMDNS, label)
			}
		}
		out = append(out, byte(len(label)))
		out = append(out, label...)
	}
	out = append(out, 0)
	return out, nil
}

// EncodeMDNSQuery builds a DNS-SD PTR question for the Grove Connect service.
// Empty service defaults to ServiceType. The packet carries no secrets.
func EncodeMDNSQuery(service string) ([]byte, error) {
	if service == "" {
		service = ServiceType
	}
	qname := service
	if !strings.HasSuffix(service, ".local") {
		qname = service + ".local"
	}
	name, err := EncodeDNSName(qname)
	if err != nil {
		return nil, err
	}
	question := make([]byte, 0, len(name)+4)
	question = append(question, name...)
	var rr [4]byte
	binary.BigEndian.PutUint16(rr[0:2], dnsPTR)
	binary.BigEndian.PutUint16(rr[2:4], dnsIN)
	question = append(question, rr[:]...)

	header := make([]byte, 12)
	binary.BigEndian.PutUint16(header[4:6], 1) // QDCOUNT
	return append(header, question...), nil
}

func txtRData(entries []string) ([]byte, error) {
	var out []byte
	for _, item := range entries {
		for _, r := range item {
			if r > unicode.MaxASCII {
				return nil, fmt.Errorf("%w: TXT string not ASCII", ErrMDNS)
			}
		}
		raw := []byte(item)
		if len(raw) > 255 {
			return nil, fmt.Errorf("%w: TXT string too long", ErrMDNS)
		}
		out = append(out, byte(len(raw)))
		out = append(out, raw...)
	}
	return out, nil
}

func resourceRecord(name string, rtype uint16, ttl uint32, rdata []byte) ([]byte, error) {
	enc, err := EncodeDNSName(name)
	if err != nil {
		return nil, err
	}
	hdr := make([]byte, 10)
	binary.BigEndian.PutUint16(hdr[0:2], rtype)
	binary.BigEndian.PutUint16(hdr[2:4], dnsIN)
	binary.BigEndian.PutUint32(hdr[4:8], ttl)
	binary.BigEndian.PutUint16(hdr[8:10], uint16(len(rdata)))
	out := make([]byte, 0, len(enc)+len(hdr)+len(rdata))
	out = append(out, enc...)
	out = append(out, hdr...)
	out = append(out, rdata...)
	return out, nil
}

// EncodeMDNSAnnounce builds a multicast DNS-SD announcement. TXT is host-pub hash only.
func EncodeMDNSAnnounce(bindHost string, port int, hostPub []byte) ([]byte, error) {
	hp := HostPubHash(hostPub)
	instance := "Remedy-" + hp + "." + ServiceName
	target := "remedy-" + hp + ".local"
	txt, err := txtRData([]string{"hp=" + hp})
	if err != nil {
		return nil, err
	}
	targetEnc, err := EncodeDNSName(target)
	if err != nil {
		return nil, err
	}
	srv := make([]byte, 6, 6+len(targetEnc))
	binary.BigEndian.PutUint16(srv[0:2], 0) // priority
	binary.BigEndian.PutUint16(srv[2:4], 0) // weight
	binary.BigEndian.PutUint16(srv[4:6], uint16(port&0xffff))
	srv = append(srv, targetEnc...)

	host, err := advertiseIPv4(bindHost)
	if err != nil {
		return nil, err
	}
	ip := net.ParseIP(host).To4()
	if ip == nil {
		return nil, fmt.Errorf("%w: mDNS advertise needs a chosen IPv4, not %q", ErrMDNS, bindHost)
	}

	instanceEnc, err := EncodeDNSName(instance)
	if err != nil {
		return nil, err
	}
	ptr, err := resourceRecord(ServiceName, dnsPTR, mdnsTTL, instanceEnc)
	if err != nil {
		return nil, err
	}
	srvRR, err := resourceRecord(instance, dnsSRV, mdnsTTL, srv)
	if err != nil {
		return nil, err
	}
	txtRR, err := resourceRecord(instance, dnsTXT, mdnsTTL, txt)
	if err != nil {
		return nil, err
	}
	aRR, err := resourceRecord(target, dnsA, mdnsTTL, []byte(ip))
	if err != nil {
		return nil, err
	}

	answers := append(append(append(ptr, srvRR...), txtRR...), aRR...)
	header := make([]byte, 12)
	binary.BigEndian.PutUint16(header[2:4], mdnsResponseFlags)
	binary.BigEndian.PutUint16(header[6:8], 4) // ANCOUNT
	return append(header, answers...), nil
}

// StartAdvertiser advertises _remedy-connect._udp on the same L2.
// Returns a stop function. Live send failures are swallowed so locked-down NICs
// still get a working stop callback. Bind failure fail-closes: no-op stop, nil error
// (server stays up; no fake multicast). Invalid bind host returns an error.
func StartAdvertiser(bindHost string, port int, hostPub []byte) (stop func(), err error) {
	host, err := advertiseIPv4(bindHost)
	if err != nil {
		return nil, err
	}
	packet, err := EncodeMDNSAnnounce(host, port, hostPub)
	if err != nil {
		return nil, err
	}

	conn, err := net.ListenUDP("udp4", &net.UDPAddr{IP: net.ParseIP(host), Port: 0})
	if err != nil {
		return func() {}, nil
	}

	done := make(chan struct{})
	var once sync.Once
	var wg sync.WaitGroup
	dst := &net.UDPAddr{IP: net.ParseIP(MDNSGroup), Port: MDNSPort}

	wg.Add(1)
	go func() {
		defer wg.Done()
		defer func() { _ = conn.Close() }()
		for {
			_, _ = conn.WriteToUDP(packet, dst)
			timer := time.NewTimer(mdnsAnnounceEvery)
			select {
			case <-done:
				timer.Stop()
				return
			case <-timer.C:
			}
		}
	}()

	stop = func() {
		once.Do(func() {
			close(done)
			_ = conn.Close()
		})
		wait := make(chan struct{})
		go func() {
			wg.Wait()
			close(wait)
		}()
		select {
		case <-wait:
		case <-time.After(mdnsStopJoin):
		}
	}
	return stop, nil
}
