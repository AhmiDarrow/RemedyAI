//go:build linux

package secret

import (
	"net/netip"
	"strings"
	"testing"
)

func TestFindSocketUIDMatchesClientEnd(t *testing.T) {
	// 127.0.0.1:54321 -> 127.0.0.1:7400 owned by uid 1001; the server side of
	// the same connection (uid 1000) must not be picked.
	table := `  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode
   0: 0100007F:1CE8 00000000:0000 0A 00000000:00000000 00:00000000 00000000  1000        0 11 1 0 100 0 0 10 0
   1: 0100007F:1CE8 0100007F:D431 01 00000000:00000000 00:00000000 00000000  1000        0 12 1 0 20 4 30 10 -1
   2: 0100007F:D431 0100007F:1CE8 01 00000000:00000000 00:00000000 00000000  1001        0 13 1 0 20 4 30 10 -1
`
	client := netip.MustParseAddrPort("127.0.0.1:54321")
	server := netip.MustParseAddrPort("127.0.0.1:7400")
	uid, found, err := findSocketUID(strings.NewReader(table), client, server)
	if err != nil || !found || uid != 1001 {
		t.Fatalf("uid=%d found=%v err=%v", uid, found, err)
	}
	if _, found, _ := findSocketUID(strings.NewReader(table), netip.MustParseAddrPort("127.0.0.1:1"), server); found {
		t.Fatal("unrelated endpoint matched")
	}
}

func TestParseProcAddrIPv6Loopback(t *testing.T) {
	got, err := parseProcAddr("00000000000000000000000001000000:1CE8")
	if err != nil {
		t.Fatal(err)
	}
	if got != netip.MustParseAddrPort("[::1]:7400") {
		t.Fatalf("got %v", got)
	}
	mapped, err := parseProcAddr("0000000000000000FFFF00000100007F:1CE8")
	if err != nil || mapped != netip.MustParseAddrPort("127.0.0.1:7400") {
		t.Fatalf("mapped %v %v", mapped, err)
	}
}
