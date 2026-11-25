package connect

import (
	"net"
	"testing"
)

func TestPairingOnlyFromHomeNetwork(t *testing.T) {
	for addr, want := range map[string]bool{
		"127.0.0.1": true, "192.168.0.46": true, "10.1.2.3": true, "fe80::1": true,
		"8.8.8.8": false, "100.64.0.9": false, "2001:db8::1": false,
	} {
		if got := pairableAddr(&net.TCPAddr{IP: net.ParseIP(addr), Port: 1}); got != want {
			t.Errorf("%s: pairable=%v, want %v", addr, got, want)
		}
	}
	// Away from home a pairing payload cannot create a device, valid code or not.
	kp, _ := GenerateKeyPair()
	if _, err := authenticatePayload([]byte("pair:"+"00000000000000000000000000000000"), kp.Public, t.TempDir(), false); err == nil {
		t.Fatal("pairing was accepted on a remote path")
	}
}
