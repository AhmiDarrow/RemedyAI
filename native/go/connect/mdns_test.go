package connect_test

import (
	"bytes"
	"encoding/hex"
	"errors"
	"testing"

	"github.com/AhmiDarrow/RemedyAI/native/go/connect"
)

var (
	mdnsHostPub = func() []byte {
		b := make([]byte, 32)
		for i := range b {
			b[i] = byte(i)
		}
		return b
	}()
	mdnsPairSecret = "pair-secret-super-secret-do-not-leak"
	mdnsBindHost   = "192.168.1.20"
)

func TestMDNSHostPubHashIs16HexChars(t *testing.T) {
	hp := connect.HostPubHash(mdnsHostPub)
	if len(hp) != 16 {
		t.Fatalf("len=%d", len(hp))
	}
	if hp != connect.HostPubHash(mdnsHostPub) {
		t.Fatal("not stable")
	}
	if _, err := hex.DecodeString(hp); err != nil {
		t.Fatalf("not hex: %v", err)
	}
	if connect.HostPubHash(bytes.Repeat([]byte{0}, 32)) == hp {
		t.Fatal("zero key hash collided")
	}
	// Pin against the Python blake2s-256 truncate.
	if hp != "05825607d7fdf2d8" {
		t.Fatalf("hash=%s want 05825607d7fdf2d8", hp)
	}
	if connect.HostPubHash(bytes.Repeat([]byte{0}, 32)) != "320b5ea99e653bc2" {
		t.Fatalf("zero hash mismatch")
	}
}

func TestMDNSQueryContainsServiceName(t *testing.T) {
	pkt, err := connect.EncodeMDNSQuery("")
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Contains(pkt, []byte("_remedy-connect")) {
		t.Fatal("missing service type")
	}
	if !bytes.Contains(pkt, []byte("_udp")) {
		t.Fatal("missing _udp")
	}
	if bytes.Contains(pkt, []byte("Bearer")) || bytes.Contains(pkt, []byte("local_api_token")) {
		t.Fatal("secrets leaked into query")
	}
	want, err := hex.DecodeString("0000000000010000000000000f5f72656d6564792d636f6e6e656374045f756470056c6f63616c00000c0001")
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(pkt, want) {
		t.Fatalf("query bytes\n got %x\nwant %x", pkt, want)
	}
}

func TestMDNSAnnounceContainsServiceAndHashNotSecrets(t *testing.T) {
	pkt, err := connect.EncodeMDNSAnnounce(mdnsBindHost, 7401, mdnsHostPub)
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Contains(pkt, []byte("_remedy-connect")) || !bytes.Contains(pkt, []byte("_udp")) {
		t.Fatal("missing service labels")
	}
	hp := []byte(connect.HostPubHash(mdnsHostPub))
	if !bytes.Contains(pkt, hp) {
		t.Fatal("missing host-pub hash")
	}
	if bytes.Contains(pkt, mdnsHostPub) {
		t.Fatal("raw host pub in packet")
	}
	for _, secret := range [][]byte{
		[]byte("Bearer"),
		[]byte("local_api_token"),
		[]byte(mdnsPairSecret),
		[]byte("pair-secret"),
	} {
		if bytes.Contains(pkt, secret) {
			t.Fatalf("secret %q leaked", secret)
		}
	}
	want, err := hex.DecodeString("0000840000000004000000000f5f72656d6564792d636f6e6e656374045f756470056c6f63616c00000c00010000007800341752656d6564792d303538323536303764376664663264380f5f72656d6564792d636f6e6e656374045f756470056c6f63616c001752656d6564792d303538323536303764376664663264380f5f72656d6564792d636f6e6e656374045f756470056c6f63616c0000210001000000780025000000001ce91772656d6564792d30353832353630376437666466326438056c6f63616c001752656d6564792d303538323536303764376664663264380f5f72656d6564792d636f6e6e656374045f756470056c6f63616c00001000010000007800141368703d303538323536303764376664663264381772656d6564792d30353832353630376437666466326438056c6f63616c0000010001000000780004c0a80114")
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(pkt, want) {
		t.Fatalf("announce bytes\n got %x\nwant %x", pkt, want)
	}
}

func TestMDNSPairSecretNotInPacket(t *testing.T) {
	pkt, err := connect.EncodeMDNSAnnounce(mdnsBindHost, 7401, mdnsHostPub)
	if err != nil {
		t.Fatal(err)
	}
	if bytes.Contains(pkt, []byte(mdnsPairSecret)) ||
		bytes.Contains(pkt, []byte("Bearer")) ||
		bytes.Contains(pkt, []byte("local_api_token")) {
		t.Fatal("secret material in announce")
	}
	sneaky := append([]byte("Bearer local_api_token"), bytes.Repeat([]byte{0}, 10)...)
	sneakyPkt, err := connect.EncodeMDNSAnnounce(mdnsBindHost, 7401, sneaky)
	if err != nil {
		t.Fatal(err)
	}
	if bytes.Contains(sneakyPkt, []byte("Bearer")) || bytes.Contains(sneakyPkt, []byte("local_api_token")) {
		t.Fatal("stuffed ASCII pub key leaked into TXT")
	}
	if !bytes.Contains(sneakyPkt, []byte(connect.HostPubHash(sneaky))) {
		t.Fatal("expected hash of sneaky key")
	}
}

func TestMDNSStartAdvertiserReturnsStopWithoutLiveMulticast(t *testing.T) {
	stop, err := connect.StartAdvertiser("127.0.0.1", 7401, mdnsHostPub)
	if err != nil {
		t.Fatal(err)
	}
	if stop == nil {
		t.Fatal("nil stop")
	}
	stop()
	stop() // idempotent
}

func TestMDNSEncodeDNSName(t *testing.T) {
	got, err := connect.EncodeDNSName("")
	if err != nil || !bytes.Equal(got, []byte{0}) {
		t.Fatalf("empty: %x %v", got, err)
	}
	got, err = connect.EncodeDNSName(".")
	if err != nil || !bytes.Equal(got, []byte{0}) {
		t.Fatalf("dot: %x %v", got, err)
	}
	got, err = connect.EncodeDNSName("  Foo.Bar.  ")
	if err != nil {
		t.Fatal(err)
	}
	if hex.EncodeToString(got) != "03466f6f0342617200" {
		t.Fatalf("got %x", got)
	}
	_, err = connect.EncodeDNSName(string(bytes.Repeat([]byte{'a'}, 64)) + ".local")
	if !errors.Is(err, connect.ErrMDNS) {
		t.Fatalf("long label: %v", err)
	}
}

func TestMDNSAnnounceRejectsNonChosenIPv4(t *testing.T) {
	for _, host := range []string{"0.0.0.0", "::1", "office-pc", ""} {
		_, err := connect.EncodeMDNSAnnounce(host, 7401, mdnsHostPub)
		if err == nil {
			t.Fatalf("%q: expected error", host)
		}
	}
}

func TestMDNSStartAdvertiserRejectsWildcard(t *testing.T) {
	_, err := connect.StartAdvertiser("0.0.0.0", 7401, mdnsHostPub)
	if err == nil {
		t.Fatal("expected error")
	}
}
