package connect

import (
	"bytes"
	"context"
	"net"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

func TestIrohHopRequiresToken(t *testing.T) {
	token := bytes.Repeat([]byte{7}, IrohTokenLen)
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	var admitted atomic.Int32
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan struct{})
	go func() {
		defer close(done)
		irohAcceptLoop(ctx, ln, token, func(_ context.Context, c net.Conn) error {
			admitted.Add(1)
			_, _ = c.Write([]byte("ok"))
			return nil
		})
	}()

	try := func(prefix []byte) bool {
		c, err := net.Dial("tcp", ln.Addr().String())
		if err != nil {
			t.Fatal(err)
		}
		defer c.Close()
		_, _ = c.Write(prefix)
		if len(prefix) < IrohTokenLen {
			// A short write must not be admitted; close our side so the
			// read fails instead of waiting for the hop timeout.
			_ = c.(*net.TCPConn).CloseWrite()
		}
		_ = c.SetReadDeadline(time.Now().Add(2 * time.Second))
		buf := make([]byte, 2)
		n, _ := c.Read(buf)
		return n == 2 && string(buf) == "ok"
	}

	if try(bytes.Repeat([]byte{8}, IrohTokenLen)) {
		t.Fatal("wrong token admitted")
	}
	if try(token[:10]) {
		t.Fatal("short token admitted")
	}
	if !try(token) {
		t.Fatal("right token refused")
	}
	if got := admitted.Load(); got != 1 {
		t.Fatalf("admitted=%d want 1", got)
	}
	cancel()
	_ = ln.Close()
	<-done
}

func TestIrohKeyPersists(t *testing.T) {
	home := t.TempDir()
	a, err := LoadOrCreateIrohKey(home)
	if err != nil {
		t.Fatal(err)
	}
	b, err := LoadOrCreateIrohKey(home)
	if err != nil {
		t.Fatal(err)
	}
	if len(a) != 32 || !bytes.Equal(a, b) {
		t.Fatalf("iroh key not stable: %x vs %x", a, b)
	}
	host, err := LoadOrCreateHostKeyPair(home)
	if err != nil {
		t.Fatal(err)
	}
	if bytes.Equal(host.Private, a) {
		t.Fatal("iroh key must be independent of the Noise host key")
	}
}

func TestPairQRAdvertisesIroh(t *testing.T) {
	home := t.TempDir()
	t.Setenv("REMEDY_HOME", home)
	ResetPairStateForTest()
	t.Cleanup(ResetPairStateForTest)
	t.Cleanup(func() { setIrohAdvert(IrohAdvert{}) })

	opts := PairStartOpts{Loopback: true, BindHost: "127.0.0.1", BindPort: 7401, Home: home}
	qr, err := StartPair(opts)
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(qr, "\nir=") {
		t.Fatalf("ir= advertised while iroh is off: %q", qr)
	}

	setIrohAdvert(IrohAdvert{EndpointID: "abc123", RelayMode: "custom", RelayURL: "https://relay.example.net/",
		Addrs: []string{"203.0.113.7:7402", "192.168.0.46:7402"}})
	qr, err = StartPair(opts)
	if err != nil {
		t.Fatal(err)
	}
	for _, want := range []string{"\nir=abc123\n", "\nira=203.0.113.7:7402,192.168.0.46:7402\n", "\nirr=https://relay.example.net/\n"} {
		if !strings.Contains(qr, want) {
			t.Fatalf("missing %q in %q", want, qr)
		}
	}
	lines := strings.Split(qr, "\n")
	if !strings.HasPrefix(lines[len(lines)-1], "exp=") {
		t.Fatalf("exp must stay last: %q", qr)
	}
}

func TestIrohSettingsAndLockKeys(t *testing.T) {
	cfg := SettingsFromMap(map[string]any{
		"connect_iroh_enabled":   true,
		"connect_iroh_relay_url": " https://relay.example.net/ ",
	})
	if !cfg.Iroh || cfg.IrohRelayURL != "https://relay.example.net/" {
		t.Fatalf("cfg=%+v", cfg)
	}
	if SettingsFromMap(nil).Iroh {
		t.Fatal("iroh must default off")
	}
	for _, k := range []string{"connect_iroh_enabled", "connect_iroh_relay_url"} {
		if _, ok := settingsLockKeys[k]; !ok {
			t.Fatalf("%s must be locked from the phone", k)
		}
	}
}

func TestIrohSupervisorWithoutBinaryIsNoop(t *testing.T) {
	t.Setenv(IrohBinEnv, "")
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	err := RunIrohSupervisor(ctx, IrohSupervisorOpts{
		Home:    t.TempDir(),
		Bin:     "",
		Session: func(context.Context, net.Conn) error { return nil },
	})
	if err != nil {
		t.Fatal(err)
	}
}

func TestIrohRelayModeDefaultsOff(t *testing.T) {
	cases := map[[2]string]string{
		{"", ""}:                      "off",
		{"", "https://r.example/"}:    "custom",
		{"n0", ""}:                    "n0",
		{"OFF", "https://r.example/"}: "off",
		{"bogus", ""}:                 "off",
	}
	for in, want := range cases {
		if got := irohRelayMode(in[0], in[1]); got != want {
			t.Fatalf("irohRelayMode(%q,%q)=%q want %q", in[0], in[1], got, want)
		}
	}
}

func TestOrderIrohAddrsPublicFirst(t *testing.T) {
	got := orderIrohAddrs([]string{
		"127.0.0.1:7402", "192.168.0.46:7402", "[fe80::1]:7402",
		"172.23.96.1:7402", "garbage",
	}, "74.167.84.108:7402")
	if len(got) == 0 || got[0] != "74.167.84.108:7402" {
		t.Fatalf("mapped address must lead: %v", got)
	}
	for _, bad := range []string{"127.0.0.1:7402", "[fe80::1]:7402", "garbage"} {
		for _, g := range got {
			if g == bad {
				t.Fatalf("%s must be dropped: %v", bad, got)
			}
		}
	}
}

func TestClassifyReach(t *testing.T) {
	cases := []struct {
		name string
		in   ReachInput
		want string
	}{
		{"mapped public, stun agrees", ReachInput{Port: 7402, Mapped: "74.167.84.108:7402", PublicIP: "74.167.84.108"}, ReachMapped},
		{"mapped public, stun failed", ReachInput{Mapped: "74.167.84.108:7402"}, ReachMapped},
		{"mapped cgnat", ReachInput{Mapped: "100.70.1.2:7402", PublicIP: "74.167.84.108"}, ReachCGNAT},
		{"stun cgnat", ReachInput{PublicIP: "100.100.1.1"}, ReachCGNAT},
		{"double nat", ReachInput{Mapped: "74.167.84.108:7402", PublicIP: "198.51.100.9"}, ReachDoubleNAT},
		{"mapped private", ReachInput{Mapped: "10.0.0.5:7402"}, ReachDoubleNAT},
		{"ipv6 only", ReachInput{GlobalV6: []string{"2a00:1450::1"}}, ReachIPv6},
		{"nothing", ReachInput{Port: 7402, LANIP: "192.168.0.46", PublicIP: "74.167.84.108"}, ReachManual},
	}
	for _, c := range cases {
		got, msg, steps := ClassifyReach(c.in)
		if got != c.want {
			t.Fatalf("%s: verdict %s want %s (%s)", c.name, got, c.want, msg)
		}
		if msg == "" {
			t.Fatalf("%s: empty message", c.name)
		}
		if got == ReachManual && !strings.Contains(strings.Join(steps, " "), "UDP port 7402 to 192.168.0.46") {
			t.Fatalf("%s: manual steps must name the exact port and target: %v", c.name, steps)
		}
		if got == ReachCGNAT && !strings.Contains(msg, "carrier-grade NAT") {
			t.Fatalf("%s: CGNAT must be said plainly: %s", c.name, msg)
		}
	}
}
