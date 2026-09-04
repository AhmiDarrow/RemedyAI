package connect_test

import (
	"errors"
	"testing"

	"github.com/AhmiDarrow/RemedyAI/native/go/connect"
)

func TestWildcardFamilyRefused(t *testing.T) {
	for _, host := range []string{"0.0.0.0", "*", "::", "[::]", "0:0:0:0:0:0:0:0", " :: ", " 0.0.0.0 ", "[::0]", "::0"} {
		if !connect.IsWildcardBind(host) {
			t.Fatalf("%q should be wildcard", host)
		}
		if _, err := connect.AssertChosenBind(host); !errors.Is(err, connect.ErrBind) {
			t.Fatalf("%q AssertChosenBind: %v", host, err)
		}
		if connect.IsChosenIPv4(host) {
			t.Fatalf("%q must not be chosen IPv4", host)
		}
	}
}

func TestEmptyBindRefused(t *testing.T) {
	for _, host := range []string{"", "   "} {
		if _, err := connect.AssertChosenBind(host); !errors.Is(err, connect.ErrBind) {
			t.Fatalf("%q: %v", host, err)
		}
		if connect.IsChosenIPv4(host) {
			t.Fatalf("%q chosen", host)
		}
	}
}

func TestUnicastIPv4Accepted(t *testing.T) {
	for _, host := range []string{"127.0.0.1", "10.0.0.5", "192.168.1.10", "172.16.0.1", "8.8.8.8", " 10.0.0.5 "} {
		if !connect.IsChosenIPv4(host) {
			t.Fatalf("%q not chosen", host)
		}
		got, err := connect.AssertChosenBind(host)
		if err != nil {
			t.Fatal(err)
		}
		if host == " 10.0.0.5 " && got != "10.0.0.5" {
			t.Fatalf("strip got %q", got)
		}
		if connect.IsWildcardBind(host) {
			t.Fatalf("%q wildcard", host)
		}
	}
}

func TestHostnameMulticastBroadcastNotChosen(t *testing.T) {
	for _, host := range []string{
		"localhost", "example.com", "host.local", "::1", "[::1]",
		"255.255.255.255", "224.0.0.1", "fe80::1", "not-an-ip", "10.0.0", "10.0.0.5.1", "0.0.0.0/0",
	} {
		if connect.IsChosenIPv4(host) {
			t.Fatalf("%q should not be chosen IPv4", host)
		}
	}
}

func TestAssertChosenBindAllowsHostname(t *testing.T) {
	got, err := connect.AssertChosenBind("  office-pc  ")
	if err != nil || got != "office-pc" {
		t.Fatalf("got %q err=%v", got, err)
	}
	if connect.IsChosenIPv4("office-pc") {
		t.Fatal("hostname must not pass IsChosenIPv4")
	}
}

func TestEnabledChosenDefaultOff(t *testing.T) {
	cases := []connect.GatewayConfig{
		{},
		{Enabled: false, Host: "127.0.0.1", Port: 7401},
		{Enabled: true, Host: "", Port: 7401},
		{Enabled: true, Host: "0.0.0.0", Port: 7401},
		{Enabled: true, Host: "*", Port: 7401},
		{Enabled: true, Host: "localhost", Port: 7401},
	}
	for _, cfg := range cases {
		ok, _, _ := connect.EnabledChosen(cfg)
		if ok {
			t.Fatalf("expected refuse for %+v", cfg)
		}
	}
}

func TestEnabledChosenAcceptsLoopback(t *testing.T) {
	ok, host, port := connect.EnabledChosen(connect.GatewayConfig{Enabled: true, Host: "127.0.0.1", Port: 7401})
	if !ok || host != "127.0.0.1" || port != 7401 {
		t.Fatalf("ok=%v host=%s port=%d", ok, host, port)
	}
}

func TestEnabledChosenInvalidPortDefaults(t *testing.T) {
	ok, host, port := connect.EnabledChosen(connect.GatewayConfig{Enabled: true, Host: "127.0.0.1", Port: 70000})
	if !ok || host != "127.0.0.1" || port != connect.DefaultBindPort {
		t.Fatalf("ok=%v host=%s port=%d", ok, host, port)
	}
}

func TestPreferLANIPv4PutsLoopbackLast(t *testing.T) {
	got := connect.PreferLANIPv4([]string{"127.0.0.1", "10.0.0.5", "192.168.1.20"})
	want := []string{"10.0.0.5", "192.168.1.20", "127.0.0.1"}
	if len(got) != len(want) {
		t.Fatalf("got %v", got)
	}
	for i := range want {
		if got[i] != want[i] {
			t.Fatalf("got %v want %v", got, want)
		}
	}
	if only := connect.PreferLANIPv4([]string{"127.0.0.1"}); len(only) != 1 || only[0] != "127.0.0.1" {
		t.Fatalf("%v", only)
	}
	if filtered := connect.PreferLANIPv4([]string{"0.0.0.0", "10.1.2.3"}); len(filtered) != 1 || filtered[0] != "10.1.2.3" {
		t.Fatalf("%v", filtered)
	}
}

func TestPreferLANIPv4PreferredDefaultRouteWins(t *testing.T) {
	rows := connect.PreferLANIPv4(
		[]string{"172.16.0.1", "192.168.0.4", "127.0.0.1"},
		"192.168.0.4",
	)
	if rows[0] != "192.168.0.4" || rows[1] != "172.16.0.1" || rows[len(rows)-1] != "127.0.0.1" {
		t.Fatalf("%v", rows)
	}
	if got := connect.PreferLANIPv4([]string{"10.0.0.5"}, "192.168.0.4"); len(got) != 1 || got[0] != "10.0.0.5" {
		t.Fatalf("%v", got)
	}
}

func TestPickDefaultIPv4SkipsLoopbackWhenLANExists(t *testing.T) {
	if got := connect.PickDefaultIPv4([]string{"127.0.0.1", "10.0.0.5", "192.168.1.20"}); got != "10.0.0.5" {
		t.Fatalf("%q", got)
	}
	if got := connect.PickDefaultIPv4([]string{"127.0.0.1"}); got != "127.0.0.1" {
		t.Fatalf("%q", got)
	}
	if got := connect.PickDefaultIPv4([]string{}); got != "" {
		t.Fatalf("%q", got)
	}
}

func TestListCandidateIPv4ExcludesWildcard(t *testing.T) {
	addrs := connect.ListCandidateIPv4()
	if len(addrs) == 0 {
		t.Fatal("expected at least loopback")
	}
	for _, ip := range addrs {
		if ip == "0.0.0.0" || ip == "*" || connect.IsWildcardBind(ip) || !connect.IsChosenIPv4(ip) {
			t.Fatalf("bad candidate %q in %v", ip, addrs)
		}
	}
	foundLoop := false
	for _, ip := range addrs {
		if ip == "127.0.0.1" {
			foundLoop = true
			break
		}
	}
	if !foundLoop {
		t.Fatalf("missing loopback: %v", addrs)
	}
}

func TestReachableLANHostDemotesVirtualNAT(t *testing.T) {
	// Without a forced route mock we can still assert non-virtual picks stick.
	if got := connect.ReachableLANHost("10.1.2.3"); got != "10.1.2.3" {
		t.Fatalf("%q", got)
	}
	if got := connect.ReachableLANHost("192.168.5.9"); got != "192.168.5.9" {
		t.Fatalf("%q", got)
	}
}

func TestTailscaleIPv4EmptyOrCGNAT(t *testing.T) {
	ts := connect.TailscaleIPv4()
	if ts == "" {
		return
	}
	if !connect.IsChosenIPv4(ts) {
		t.Fatalf("%q", ts)
	}
}
