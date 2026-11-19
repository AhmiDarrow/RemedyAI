package connect

import (
	"context"
	"crypto/rand"
	"fmt"
	"net"
	"net/netip"
	"strconv"
	"strings"
	"sync"
	"time"
)

// Reachability self-test for "Reach this PC from mobile data".
//
// The PC is its own iroh endpoint on a fixed UDP port. A phone on mobile data
// can dial it only if that port is reachable from the internet: a router port
// mapping (UPnP-IGD / PCP / NAT-PMP, done by the sidecar), a manual forward,
// or a global IPv6 address. Behind carrier-grade NAT (many ISPs, Starlink,
// most mobile) none of these work, and the test must say so plainly.
//
// Evidence used, all local or STUN only (STUN is an address lookup, it never
// relays traffic):
//   - the router's answer to the mapping request (which protocols, and the
//     external address it claims);
//   - the public address a STUN server sees for this PC;
//   - global IPv6 addresses on this PC.

// IrohDefaultPort is the fixed UDP port the PC's iroh endpoint listens on.
const IrohDefaultPort = 7402

// DefaultSTUNServers are lookup-only servers for the public-address check.
var DefaultSTUNServers = []string{"stun.l.google.com:19302", "stun.cloudflare.com:3478"}

// Reachability verdicts.
const (
	ReachChecking  = "checking"
	ReachMapped    = "reachable"      // router mapping on a public address
	ReachIPv6      = "reachable_ipv6" // global IPv6, no mapping needed (firewall permitting)
	ReachCGNAT     = "cgnat"          // provider-shared address; inbound impossible
	ReachDoubleNAT = "double_nat"     // mapping granted on a router that is itself behind NAT
	ReachManual    = "needs_forward"  // no mapping protocol answered
	ReachOff       = "off"
)

// IrohStatus is the owner-facing state of the mobile-data endpoint.
type IrohStatus struct {
	Enabled    bool     `json:"enabled"`
	EndpointID string   `json:"endpoint_id,omitempty"`
	Port       int      `json:"port,omitempty"`
	RelayMode  string   `json:"relay_mode,omitempty"`
	Addrs      []string `json:"addrs,omitempty"`
	Mapped     string   `json:"mapped,omitempty"`
	MapProtos  []string `json:"map_protocols,omitempty"`
	PublicIP   string   `json:"public_ip,omitempty"`
	GlobalV6   []string `json:"global_ipv6,omitempty"`
	Verdict    string   `json:"verdict"`
	Message    string   `json:"message"`
	Steps      []string `json:"steps,omitempty"`
	CheckedAt  int64    `json:"checked_at,omitempty"`
}

var (
	irohStatusMu sync.Mutex
	irohStatus   = IrohStatus{Verdict: ReachOff, Message: "Reach from mobile data is off."}
)

// CurrentIrohStatus returns the latest self-test result.
func CurrentIrohStatus() IrohStatus {
	irohStatusMu.Lock()
	defer irohStatusMu.Unlock()
	s := irohStatus
	s.Addrs = append([]string(nil), s.Addrs...)
	s.MapProtos = append([]string(nil), s.MapProtos...)
	s.GlobalV6 = append([]string(nil), s.GlobalV6...)
	s.Steps = append([]string(nil), s.Steps...)
	return s
}

func updateIrohStatus(f func(*IrohStatus)) {
	irohStatusMu.Lock()
	f(&irohStatus)
	irohStatusMu.Unlock()
}

// STUNPublicAddr asks one STUN server which public address it sees.
func STUNPublicAddr(ctx context.Context, server string) (string, int, error) {
	d := net.Dialer{}
	c, err := d.DialContext(ctx, "udp4", server)
	if err != nil {
		return "", 0, err
	}
	defer c.Close()
	if dl, ok := ctx.Deadline(); ok {
		_ = c.SetDeadline(dl)
	} else {
		_ = c.SetDeadline(time.Now().Add(3 * time.Second))
	}
	txid := make([]byte, 12)
	if _, err := rand.Read(txid); err != nil {
		return "", 0, err
	}
	req, err := EncodeBindingRequest(txid)
	if err != nil {
		return "", 0, err
	}
	if _, err := c.Write(req); err != nil {
		return "", 0, err
	}
	buf := make([]byte, 1500)
	n, err := c.Read(buf)
	if err != nil {
		return "", 0, err
	}
	return ParseMappedAddress(buf[:n])
}

// isCGNATAddr reports RFC 6598 shared address space (100.64.0.0/10).
func isCGNATAddr(a netip.Addr) bool {
	return netip.MustParsePrefix("100.64.0.0/10").Contains(a.Unmap())
}

func isPublicAddr(a netip.Addr) bool {
	a = a.Unmap()
	return a.IsGlobalUnicast() && !a.IsPrivate() && !isCGNATAddr(a)
}

// ReachInput is the evidence ClassifyReach works from (pure, for tests).
type ReachInput struct {
	Port      int
	LANIP     string
	Mapped    string   // router-claimed external ip:port, "" when none
	MapProtos []string // protocols that answered the probe
	PublicIP  string   // what STUN saw, "" when STUN failed
	GlobalV6  []string
}

// ClassifyReach turns evidence into a verdict, a plain message and, when the
// owner can fix it, exact steps.
func ClassifyReach(in ReachInput) (verdict, message string, steps []string) {
	port := in.Port
	if port <= 0 {
		port = IrohDefaultPort
	}
	var mappedIP netip.Addr
	if in.Mapped != "" {
		if ap, err := netip.ParseAddrPort(in.Mapped); err == nil {
			mappedIP = ap.Addr()
		}
	}
	var stunIP netip.Addr
	if in.PublicIP != "" {
		stunIP, _ = netip.ParseAddr(in.PublicIP)
	}
	cgnatMsg := "Your internet provider shares one public address between many homes (carrier-grade NAT), " +
		"so a phone on mobile data cannot reach this PC directly. LAN, Tailscale and your own relay still work."

	switch {
	case mappedIP.IsValid() && isCGNATAddr(mappedIP):
		return ReachCGNAT, cgnatMsg, nil
	case stunIP.IsValid() && isCGNATAddr(stunIP):
		return ReachCGNAT, cgnatMsg, nil
	case mappedIP.IsValid() && isPublicAddr(mappedIP) && (!stunIP.IsValid() || stunIP == mappedIP.Unmap()):
		return ReachMapped, "Your phone can reach this PC from anywhere. Your router opened UDP port " +
			strconv.Itoa(port) + " automatically.", nil
	case mappedIP.IsValid() && stunIP.IsValid() && stunIP != mappedIP.Unmap():
		return ReachDoubleNAT, "Your router opened the port, but it sits behind another router or your provider's NAT " +
				"(it reports " + mappedIP.String() + ", the internet sees " + stunIP.String() + "). " +
				"If you control the outer router, forward the port there too; otherwise use LAN, Tailscale or your own relay.",
			forwardSteps(port, "the outer router", "this router's WAN address")
	case mappedIP.IsValid() && !isPublicAddr(mappedIP):
		return ReachDoubleNAT, "Your router opened the port but has no public address of its own (" + mappedIP.String() +
			"), so another router or your provider is in front of it.", forwardSteps(port, "the outer router", "this router's WAN address")
	case len(in.GlobalV6) > 0:
		return ReachIPv6, "This PC has a public IPv6 address, so phones with IPv6 (most mobile networks) can reach it " +
			"if your router's firewall allows inbound UDP " + strconv.Itoa(port) + ".", nil
	}
	lan := in.LANIP
	if lan == "" {
		lan = "this PC's LAN address"
	}
	return ReachManual, "Your router did not accept an automatic port mapping (UPnP, NAT-PMP and PCP are off or unsupported).",
		forwardSteps(port, "your router", lan)
}

func forwardSteps(port int, router, target string) []string {
	p := strconv.Itoa(port)
	return []string{
		"Open " + router + "'s admin page (often http://192.168.0.1 or http://192.168.1.1).",
		"Find Port Forwarding (sometimes under NAT, Virtual Server or Gaming).",
		"Forward UDP port " + p + " to " + target + ", port " + p + ".",
		"Save, then run this test again.",
	}
}

// runIrohSelfTest gathers STUN and IPv6 evidence and publishes the verdict.
func runIrohSelfTest(ctx context.Context, servers []string) {
	if len(servers) == 0 {
		servers = DefaultSTUNServers
	}
	cur := CurrentIrohStatus()
	publicIP := ""
	for _, s := range servers {
		sctx, cancel := context.WithTimeout(ctx, 3*time.Second)
		host, _, err := STUNPublicAddr(sctx, s)
		cancel()
		if err == nil && host != "" {
			publicIP = host
			break
		}
	}
	var v6 []string
	for _, a := range cur.Addrs {
		if ap, err := netip.ParseAddrPort(a); err == nil && ap.Addr().Is6() && isPublicAddr(ap.Addr()) {
			v6 = append(v6, ap.Addr().String())
		}
	}
	lan := ""
	for _, a := range cur.Addrs {
		if ap, err := netip.ParseAddrPort(a); err == nil && ap.Addr().Is4() && ap.Addr().IsPrivate() {
			lan = ap.Addr().String()
			break
		}
	}
	verdict, msg, steps := ClassifyReach(ReachInput{
		Port: cur.Port, LANIP: lan, Mapped: cur.Mapped, MapProtos: cur.MapProtos,
		PublicIP: publicIP, GlobalV6: v6,
	})
	updateIrohStatus(func(s *IrohStatus) {
		s.PublicIP = publicIP
		s.GlobalV6 = v6
		s.Verdict = verdict
		s.Message = msg
		s.Steps = steps
		s.CheckedAt = time.Now().Unix()
	})
	extra := ""
	if len(steps) > 0 {
		extra = " Steps: " + strings.Join(steps, " ")
	}
	logIroh(fmt.Sprintf("connect: mobile-data reach self-test: %s — %s%s", verdict, msg, extra))
}

// RecheckIrohReach re-runs the self-test now and returns the result. When
// the endpoint is off it just reports that.
func RecheckIrohReach(ctx context.Context) IrohStatus {
	if !CurrentIrohStatus().Enabled {
		return CurrentIrohStatus()
	}
	runIrohSelfTest(ctx, nil)
	return CurrentIrohStatus()
}
