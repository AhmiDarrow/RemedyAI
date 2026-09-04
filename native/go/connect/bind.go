package connect

import (
	"errors"
	"fmt"
	"net"
	"os"
	"sort"
	"strings"
)

// ErrBind is a Connect bind-policy failure (wildcard / empty / not chosen IPv4).
var ErrBind = errors.New("connect bind refused")

// WildcardHosts that must never be used for the Connect listener.
var WildcardHosts = map[string]struct{}{
	"0.0.0.0":         {},
	"::":              {},
	"[::]":            {},
	"*":               {},
	"0:0:0:0:0:0:0:0": {},
}

// Virtual NAT / CGNAT / link-local ranges a phone on the LAN cannot reach.
// Used only to demote an address when a real default-route source exists.
var virtualNATNets = []*net.IPNet{
	mustCIDR("172.16.0.0/12"),
	mustCIDR("100.64.0.0/10"),
	mustCIDR("169.254.0.0/16"),
}

var tailscaleNet = mustCIDR("100.64.0.0/10")

func mustCIDR(s string) *net.IPNet {
	_, n, err := net.ParseCIDR(s)
	if err != nil {
		panic(err)
	}
	return n
}

// IsWildcardBind reports whether host is an unspecified / catch-all bind.
func IsWildcardBind(host string) bool {
	text := strings.TrimSpace(host)
	if text == "" {
		return false
	}
	if _, ok := WildcardHosts[text]; ok {
		return true
	}
	inner := text
	if strings.HasPrefix(text, "[") && strings.HasSuffix(text, "]") {
		inner = text[1 : len(text)-1]
	}
	if _, ok := WildcardHosts[inner]; ok {
		return true
	}
	ip := net.ParseIP(inner)
	if ip == nil {
		return false
	}
	return ip.IsUnspecified()
}

// AssertChosenBind refuses empty and wildcard hosts. Returns the stripped host.
func AssertChosenBind(host string) (string, error) {
	text := strings.TrimSpace(host)
	if text == "" {
		return "", fmt.Errorf("%w: bind host is empty", ErrBind)
	}
	if IsWildcardBind(text) {
		return "", fmt.Errorf("%w: wildcard bind is not allowed", ErrBind)
	}
	return text, nil
}

// IsChosenIPv4 is true for a unicast IPv4 (including 127.0.0.1).
func IsChosenIPv4(host string) bool {
	text := strings.TrimSpace(host)
	if text == "" || IsWildcardBind(text) {
		return false
	}
	ip := net.ParseIP(text)
	if ip == nil {
		return false
	}
	v4 := ip.To4()
	if v4 == nil {
		return false
	}
	if ip.IsUnspecified() || ip.IsMulticast() {
		return false
	}
	if v4[0] == 255 && v4[1] == 255 && v4[2] == 255 && v4[3] == 255 {
		return false
	}
	return true
}

// IsLoopbackIPv4 reports whether host is a loopback IPv4.
func IsLoopbackIPv4(host string) bool {
	text := strings.TrimSpace(host)
	ip := net.ParseIP(text)
	if ip == nil {
		return false
	}
	v4 := ip.To4()
	if v4 == nil {
		return false
	}
	return ip.IsLoopback()
}

func ipv4Key(ip string) [4]int {
	parts := strings.Split(ip, ".")
	if len(parts) != 4 {
		return [4]int{}
	}
	var key [4]int
	for i, p := range parts {
		n := 0
		for _, c := range p {
			if c < '0' || c > '9' {
				return [4]int{}
			}
			n = n*10 + int(c-'0')
		}
		key[i] = n
	}
	return key
}

func sortIPv4(addrs []string) {
	sort.Slice(addrs, func(i, j int) bool {
		a, b := ipv4Key(addrs[i]), ipv4Key(addrs[j])
		for k := 0; k < 4; k++ {
			if a[k] != b[k] {
				return a[k] < b[k]
			}
		}
		return false
	})
}

// PreferLANIPv4 returns LAN unicast first (preferred route sources ahead of
// other LAN), then loopback. Drops non-chosen addresses.
func PreferLANIPv4(addrs []string, preferred ...string) []string {
	lan := make([]string, 0, len(addrs))
	loop := make([]string, 0, 1)
	seen := make(map[string]struct{}, len(addrs))
	for _, raw := range addrs {
		ip := strings.TrimSpace(raw)
		if ip == "" {
			continue
		}
		if _, ok := seen[ip]; ok {
			continue
		}
		if !IsChosenIPv4(ip) {
			continue
		}
		seen[ip] = struct{}{}
		if IsLoopbackIPv4(ip) {
			loop = append(loop, ip)
		} else {
			lan = append(lan, ip)
		}
	}
	pre := make([]string, 0, len(preferred))
	preSet := make(map[string]struct{}, len(preferred))
	for _, p := range preferred {
		ip := strings.TrimSpace(p)
		if ip == "" {
			continue
		}
		if _, ok := seen[ip]; !ok {
			continue
		}
		if _, ok := preSet[ip]; ok {
			continue
		}
		preSet[ip] = struct{}{}
		pre = append(pre, ip)
	}
	rest := make([]string, 0, len(lan))
	for _, ip := range lan {
		if _, ok := preSet[ip]; !ok {
			rest = append(rest, ip)
		}
	}
	sortIPv4(pre)
	sortIPv4(rest)
	sortIPv4(loop)
	out := make([]string, 0, len(pre)+len(rest)+len(loop))
	out = append(out, pre...)
	out = append(out, rest...)
	out = append(out, loop...)
	return out
}

// DefaultRouteIPv4 is the source IPv4 the OS would use to reach the internet.
// Empty when no route is available.
func DefaultRouteIPv4() string {
	for _, dest := range []string{"8.8.8.8:80", "1.1.1.1:80"} {
		conn, err := net.Dial("udp", dest)
		if err != nil {
			continue
		}
		local, ok := conn.LocalAddr().(*net.UDPAddr)
		_ = conn.Close()
		if !ok || local == nil || local.IP == nil {
			continue
		}
		ip := local.IP.To4()
		if ip == nil {
			continue
		}
		text := ip.String()
		if IsChosenIPv4(text) {
			return text
		}
	}
	return ""
}

func isVirtualNATIPv4(host string) bool {
	ip := net.ParseIP(strings.TrimSpace(host))
	if ip == nil {
		return false
	}
	v4 := ip.To4()
	if v4 == nil {
		return false
	}
	for _, n := range virtualNATNets {
		if n.Contains(v4) {
			return true
		}
	}
	return false
}

// PickDefaultIPv4 returns the first LAN unicast from addrs (or from
// ListCandidateIPv4 when addrs is nil). Loopback only when alone.
func PickDefaultIPv4(addrs []string) string {
	var rows []string
	if addrs == nil {
		rows = ListCandidateIPv4()
	} else {
		rows = PreferLANIPv4(addrs)
	}
	for _, ip := range rows {
		if !IsLoopbackIPv4(ip) {
			return ip
		}
	}
	if len(rows) > 0 {
		return rows[0]
	}
	return ""
}

// ReachableLANHost picks the best LAN host to advertise for phone pairing.
// Virtual-NAT / loopback / empty configured hosts are demoted to the default
// route when one exists; an explicit non-virtual pick is kept.
func ReachableLANHost(configured string) string {
	cfg := strings.TrimSpace(configured)
	route := DefaultRouteIPv4()
	if route != "" && (cfg == "" || IsLoopbackIPv4(cfg) || isVirtualNATIPv4(cfg)) {
		return route
	}
	if cfg != "" {
		return cfg
	}
	return PickDefaultIPv4(nil)
}

// ListCandidateIPv4 returns host IPv4s excluding wildcards. Default-route
// source first; 127.0.0.1 last.
func ListCandidateIPv4() []string {
	found := map[string]struct{}{"127.0.0.1": {}}
	hostname, err := osHostname()
	if err == nil && hostname != "" {
		if addrs, err := net.LookupHost(hostname); err == nil {
			for _, ip := range addrs {
				if IsChosenIPv4(ip) {
					found[ip] = struct{}{}
				}
			}
		}
		if infos, err := net.LookupIP(hostname); err == nil {
			for _, ip := range infos {
				v4 := ip.To4()
				if v4 == nil {
					continue
				}
				text := v4.String()
				if IsChosenIPv4(text) {
					found[text] = struct{}{}
				}
			}
		}
	}
	route := DefaultRouteIPv4()
	if route != "" {
		found[route] = struct{}{}
	}
	addrs := make([]string, 0, len(found))
	for ip := range found {
		addrs = append(addrs, ip)
	}
	if route != "" {
		return PreferLANIPv4(addrs, route)
	}
	return PreferLANIPv4(addrs)
}

// IsGlobalIPv6 is true for a global unicast IPv6 (not link-local, ULA,
// loopback, or wildcard).
func IsGlobalIPv6(host string) bool {
	text := strings.TrimSpace(host)
	text = strings.Trim(text, "[]")
	if i := strings.IndexByte(text, '%'); i >= 0 {
		text = text[:i]
	}
	if text == "" || IsWildcardBind(text) {
		return false
	}
	ip := net.ParseIP(text)
	if ip == nil || ip.To4() != nil {
		return false
	}
	return ip.IsGlobalUnicast()
}

// ListCandidateIPv6 returns global unicast IPv6 addresses on this host.
func ListCandidateIPv6() []string {
	found := map[string]struct{}{}
	hostname, err := osHostname()
	if err != nil || hostname == "" {
		return nil
	}
	infos, err := net.LookupIP(hostname)
	if err != nil {
		return nil
	}
	for _, ip := range infos {
		if ip.To4() != nil {
			continue
		}
		text := ip.String()
		if i := strings.IndexByte(text, '%'); i >= 0 {
			text = text[:i]
		}
		if IsGlobalIPv6(text) {
			found[text] = struct{}{}
		}
	}
	out := make([]string, 0, len(found))
	for ip := range found {
		out = append(out, ip)
	}
	sort.Strings(out)
	return out
}

// TailscaleIPv4 returns an IPv4 on the Tailscale CGNAT range, or "".
func TailscaleIPv4() string {
	for _, ip := range ListCandidateIPv4() {
		parsed := net.ParseIP(ip)
		if parsed == nil {
			continue
		}
		v4 := parsed.To4()
		if v4 != nil && tailscaleNet.Contains(v4) {
			return ip
		}
	}
	return ""
}

// osHostname is overridable in tests.
var osHostname = os.Hostname
