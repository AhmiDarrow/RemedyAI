package connect_test

import (
	"bytes"
	"encoding/hex"
	"strings"
	"testing"

	"github.com/AhmiDarrow/RemedyAI/native/go/connect"
)

func TestParseRelayEndpointHostPort(t *testing.T) {
	cases := []struct {
		in   string
		host string
		port int
	}{
		{"192.0.2.9:7402", "192.0.2.9", 7402},
		{"tcp://192.0.2.9:7402", "192.0.2.9", 7402},
		{"relay.example.com:7402", "relay.example.com", 7402},
		{"192.0.2.9", "192.0.2.9", connect.DefaultRelayPort},
		{"[2001:db8::1]:7402", "2001:db8::1", 7402},
		{"2001:db8::1", "2001:db8::1", connect.DefaultRelayPort},
	}
	for _, tc := range cases {
		host, port, err := connect.ParseRelayEndpoint(tc.in)
		if err != nil {
			t.Fatalf("%q: %v", tc.in, err)
		}
		if host != tc.host || port != tc.port {
			t.Fatalf("%q: got %s:%d want %s:%d", tc.in, host, port, tc.host, tc.port)
		}
	}
}

func TestParseRelayEndpointRefusesHTTPCredentialsQueryWildcard(t *testing.T) {
	bads := []struct {
		in   string
		want string
	}{
		{"https://example.com/relay", "HTTP"},
		{"tcp://user:pass@192.0.2.9:7402", "credentials"},
		{"tcp://192.0.2.9:7402?token=1", "query"},
		{"0.0.0.0:7402", "wildcard"},
		{"192.0.2.9:7402/Bearer abc", "secrets"},
		{"", "empty"},
	}
	for _, tc := range bads {
		_, _, err := connect.ParseRelayEndpoint(tc.in)
		if err == nil || !strings.Contains(err.Error(), tc.want) {
			t.Fatalf("%q: want error containing %q, got %v", tc.in, tc.want, err)
		}
	}
}

func TestSessionIDPairAndDevice(t *testing.T) {
	hp := bytes.Repeat([]byte{0x11}, 32)
	ps := bytes.Repeat([]byte{0x22}, 32)
	dp := bytes.Repeat([]byte{0x33}, 32)

	a, err := connect.SessionIDPair(hp, ps)
	if err != nil {
		t.Fatal(err)
	}
	b, err := connect.SessionIDDevice(hp, dp)
	if err != nil {
		t.Fatal(err)
	}
	if len(a) != connect.SessionIDLen || len(b) != connect.SessionIDLen {
		t.Fatalf("lengths a=%d b=%d", len(a), len(b))
	}
	if bytes.Equal(a, b) {
		t.Fatal("pair and device ids must differ")
	}
	a2, err := connect.SessionIDPair(hp, ps)
	if err != nil || !bytes.Equal(a, a2) {
		t.Fatalf("pair id not stable: %v", err)
	}
	if bytes.Contains(a, bytes.Repeat([]byte{0x22}, 4)) {
		t.Fatal("pair secret must be hashed, not inlined")
	}

	// Pin against Python hashlib.blake2s(digest_size=16).
	if got := hex.EncodeToString(a); got != "e229bee6bf153948890c3ded537e572e" {
		t.Fatalf("pair id hex=%s", got)
	}
	if got := hex.EncodeToString(b); got != "6b7ef4f169a84d6d83a3b28ff6722d0b" {
		t.Fatalf("device id hex=%s", got)
	}
}

func TestSessionIDRejectsWrongLengths(t *testing.T) {
	hp := bytes.Repeat([]byte{0x11}, 32)
	if _, err := connect.SessionIDPair(hp, []byte("short")); err == nil {
		t.Fatal("expected length error")
	}
	if _, err := connect.SessionIDDevice([]byte("x"), hp); err == nil {
		t.Fatal("expected length error")
	}
}
