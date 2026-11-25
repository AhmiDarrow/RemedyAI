package tools

import (
	"context"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/netip"
	"net/url"
	"strings"
	"time"
)

// SSRF guard and pinned transport for web.fetch / web.search, ported from
// src/remedy/core/web_helpers.py. Resolve DNS once, require every A/AAAA to be
// public, dial the vetted IP with the original Host header and TLS ServerName
// (no second lookup between check and connect), and re-validate every
// redirect hop the same way.

const (
	webMaxRedirects       = 8
	webRedirectDrainBytes = 8192
)

var webRedirectStatus = map[int]bool{301: true, 302: true, 303: true, 307: true, 308: true}

// webBlockedPrefixes are the non-globally-routable ranges (IANA special
// purpose registries, what Python's ipaddress is_global/is_private/
// is_reserved/is_multicast/is_unspecified refuse). Fail closed: a few ranges
// Python counts as global by exception (inside 192.0.0.0/24 and 2001::/23) are
// blocked here too, as are deprecated site-local fec0::/10 and 6to4 2002::/16,
// which can carry a private IPv4 address.
var webBlockedPrefixes = func() []netip.Prefix {
	raw := []string{
		// IPv4
		"0.0.0.0/8", "10.0.0.0/8", "100.64.0.0/10", "127.0.0.0/8",
		"169.254.0.0/16", "172.16.0.0/12", "192.0.0.0/24", "192.0.2.0/24",
		"192.88.99.0/24", "192.168.0.0/16", "198.18.0.0/15", "198.51.100.0/24",
		"203.0.113.0/24", "224.0.0.0/4", "240.0.0.0/4",
		// IPv6
		"::/128", "::1/128", "::ffff:0:0/96", "64:ff9b:1::/48", "100::/64",
		"2001::/23", "2001:db8::/32", "2002::/16", "3fff::/20", "fc00::/7",
		"fe80::/10", "fec0::/10", "ff00::/8",
	}
	out := make([]netip.Prefix, 0, len(raw))
	for _, s := range raw {
		out = append(out, netip.MustParsePrefix(s))
	}
	return out
}()

var webNAT64 = netip.MustParsePrefix("64:ff9b::/96")

// webIPBlocked refuses non-public addresses. IPv4-mapped IPv6 is unwrapped
// first; NAT64 (64:ff9b::/96) is judged by the IPv4 address it carries.
func webIPBlocked(ip netip.Addr) bool {
	if !ip.IsValid() {
		return true
	}
	ip = ip.WithZone("").Unmap()
	if ip.Is6() && webNAT64.Contains(ip) {
		b := ip.As16()
		return webIPBlocked(netip.AddrFrom4([4]byte{b[12], b[13], b[14], b[15]}))
	}
	if ip.Is6() {
		// Only 2000::/3 is global unicast; everything outside it is reserved.
		if b := ip.As16(); b[0]&0xe0 != 0x20 {
			return true
		}
	}
	for _, p := range webBlockedPrefixes {
		if p.Contains(ip) {
			return true
		}
	}
	return ip.IsLoopback() || ip.IsPrivate() || ip.IsLinkLocalUnicast() ||
		ip.IsLinkLocalMulticast() || ip.IsMulticast() || ip.IsUnspecified()
}

// webHostNameBlocked is the name-only half of _host_is_blocked.
func webHostNameBlocked(hostname string) bool {
	host := strings.TrimSuffix(strings.ToLower(strings.TrimSpace(hostname)), ".")
	if host == "" {
		return true
	}
	switch host {
	case "localhost", "metadata.google.internal", "metadata.goog", "metadata", "instance-data":
		return true
	}
	if strings.HasSuffix(host, ".local") || strings.HasSuffix(host, ".internal") {
		return true
	}
	for _, label := range strings.Split(host, ".") {
		if label == "metadata" {
			return true
		}
	}
	for _, s := range []string{".nip.io", ".sslip.io", ".xip.io"} {
		if strings.HasSuffix(host, s) {
			return true
		}
	}
	return strings.HasPrefix(host, "169.254.")
}

// webClient holds the transport seams. lookup and allowAddr exist so tests can
// point a name at a loopback httptest server; production leaves allowAddr nil.
type webClient struct {
	version string
	config  func() map[string]any
	lookup  func(ctx context.Context, host string) ([]netip.Addr, error)
	// allowAddr, when set, admits an address the block list refuses. Tests only.
	allowAddr    func(netip.Addr) bool
	ddgEndpoint  string
	openserpBase string
	minDelay     time.Duration
}

func (c *webClient) userAgent() string {
	v := strings.TrimSpace(c.version)
	if v == "" {
		v = "0"
	}
	return webRobotsAgent + "/" + v + " (+https://github.com/AhmiDarrow/RemedyAI)"
}

func (c *webClient) addrBlocked(ip netip.Addr) bool {
	if c.allowAddr != nil && c.allowAddr(ip.WithZone("").Unmap()) {
		return false
	}
	return webIPBlocked(ip)
}

// resolvePublic is _host_is_blocked + _resolve_public_ips in one lookup:
// empty when the name is blocked, does not resolve, or any address is private.
func (c *webClient) resolvePublic(ctx context.Context, hostname string) []netip.Addr {
	host := strings.TrimSuffix(strings.ToLower(strings.TrimSpace(hostname)), ".")
	if webHostNameBlocked(host) {
		return nil
	}
	if ip, err := netip.ParseAddr(host); err == nil {
		if c.addrBlocked(ip) {
			return nil
		}
		return []netip.Addr{ip.WithZone("").Unmap()}
	}
	lookup := c.lookup
	if lookup == nil {
		lookup = func(ctx context.Context, h string) ([]netip.Addr, error) {
			return net.DefaultResolver.LookupNetIP(ctx, "ip", h)
		}
	}
	addrs, err := lookup(ctx, host)
	if err != nil {
		return nil
	}
	var out []netip.Addr
	seen := map[netip.Addr]bool{}
	for _, a := range addrs {
		if c.addrBlocked(a) {
			return nil // mixed public + private: fail closed
		}
		a = a.WithZone("").Unmap()
		if !seen[a] {
			seen[a] = true
			out = append(out, a)
		}
	}
	return out
}

// hostBlocked mirrors _host_is_blocked (used by the SearXNG gate).
func (c *webClient) hostBlocked(ctx context.Context, host string) bool {
	return len(c.resolvePublic(ctx, host)) == 0
}

func preferConnectIP(ips []netip.Addr) netip.Addr {
	for _, ip := range ips {
		if ip.Is4() {
			return ip
		}
	}
	return ips[0]
}

// readCapped reads up to cap+1 bytes so the caller can see truncation.
func readCapped(r io.Reader, cap int) ([]byte, error) {
	return io.ReadAll(io.LimitReader(r, int64(cap)+1))
}

func charsetOf(contentType string) string {
	low := strings.ToLower(contentType)
	i := strings.Index(low, "charset=")
	if i < 0 {
		return ""
	}
	cs := contentType[i+len("charset="):]
	if j := strings.Index(cs, ";"); j >= 0 {
		cs = cs[:j]
	}
	return strings.Trim(strings.TrimSpace(cs), `"'`)
}

func hasUserinfo(u *url.URL) bool { return u.User != nil }

// webHTTPError mirrors urllib's HTTPError text, which is what the model saw.
func webHTTPError(status int) error {
	reason := http.StatusText(status)
	if reason == "" {
		reason = "error"
	}
	return fmt.Errorf("HTTP Error %d: %s", status, reason)
}

// pinnedFetch is _pinned_fetch: returns (final_url, body, charset).
func (c *webClient) pinnedFetch(ctx context.Context, rawURL string, maxBytes int, timeout time.Duration) (string, []byte, string, error) {
	current := strings.TrimSpace(rawURL)
	for hop := 0; hop <= webMaxRedirects; hop++ {
		if err := ctx.Err(); err != nil {
			return "", nil, "", errors.New("ABORTED")
		}
		if !strings.HasPrefix(current, "http://") && !strings.HasPrefix(current, "https://") {
			return "", nil, "", errors.New("url must start with http:// or https://")
		}
		u, err := url.Parse(current)
		if err != nil {
			return "", nil, "", fmt.Errorf("<urlopen error %v>", err)
		}
		if hasUserinfo(u) {
			return "", nil, "", errors.New("URL_USERINFO_BLOCKED")
		}
		host := u.Hostname()
		if host == "" {
			return "", nil, "", errors.New("URL missing hostname")
		}
		ips := c.resolvePublic(ctx, host)
		if len(ips) == 0 {
			return "", nil, "", errors.New("SSRF_BLOCKED")
		}
		pinned := preferConnectIP(ips)
		port := u.Port()
		if port == "" {
			port = "80"
			if u.Scheme == "https" {
				port = "443"
			}
		}
		status, loc, body, charset, err := c.oneRequest(ctx, u, net.JoinHostPort(pinned.String(), port), maxBytes, timeout)
		if err != nil {
			return "", nil, "", err
		}
		if webRedirectStatus[status] {
			if loc == "" {
				return "", nil, "", fmt.Errorf("HTTP %d redirect without Location", status)
			}
			ref, err := url.Parse(loc)
			if err != nil {
				return "", nil, "", fmt.Errorf("<urlopen error %v>", err)
			}
			next := u.ResolveReference(ref)
			if hasUserinfo(next) {
				return "", nil, "", errors.New("URL_USERINFO_BLOCKED")
			}
			if len(c.resolvePublic(ctx, next.Hostname())) == 0 {
				return "", nil, "", errors.New("SSRF_BLOCKED_REDIRECT")
			}
			current = next.String()
			continue
		}
		if status >= 400 {
			return "", nil, "", webHTTPError(status)
		}
		if charset == "" {
			charset = "utf-8"
		}
		return current, body, charset, nil
	}
	return "", nil, "", errors.New("Too many redirects")
}

// oneRequest performs a single GET over a connection dialed to dialAddr (the
// vetted IP). TLS ServerName and the Host header come from u, so certificates
// are still checked against the hostname. No proxy: a proxy would resolve the
// name itself and undo the pin.
func (c *webClient) oneRequest(ctx context.Context, u *url.URL, dialAddr string, maxBytes int, timeout time.Duration) (int, string, []byte, string, error) {
	ctx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()
	dialer := &net.Dialer{Timeout: timeout}
	transport := &http.Transport{
		Proxy: nil,
		DialContext: func(ctx context.Context, network, _ string) (net.Conn, error) {
			return dialer.DialContext(ctx, network, dialAddr)
		},
		DisableKeepAlives:     true,
		DisableCompression:    true,
		ResponseHeaderTimeout: timeout,
		TLSHandshakeTimeout:   timeout,
		ForceAttemptHTTP2:     false,
	}
	defer transport.CloseIdleConnections()
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, u.String(), nil)
	if err != nil {
		return 0, "", nil, "", fmt.Errorf("<urlopen error %v>", err)
	}
	req.Header.Set("User-Agent", c.userAgent())
	req.Header.Set("Accept", "text/*,application/json,*/*")
	resp, err := transport.RoundTrip(req)
	if err != nil {
		return 0, "", nil, "", fmt.Errorf("<urlopen error %v>", err)
	}
	defer resp.Body.Close()
	switch {
	case webRedirectStatus[resp.StatusCode]:
		_, _ = readCapped(resp.Body, webRedirectDrainBytes)
		return resp.StatusCode, resp.Header.Get("Location"), nil, "", nil
	case resp.StatusCode >= 400:
		_, _ = readCapped(resp.Body, 500)
		return resp.StatusCode, "", nil, "", nil
	}
	body, err := readCapped(resp.Body, maxBytes)
	if err != nil {
		return 0, "", nil, "", fmt.Errorf("<urlopen error %v>", err)
	}
	return resp.StatusCode, "", body, charsetOf(resp.Header.Get("Content-Type")), nil
}

// directFetch is _direct_fetch: no SSRF pin, only for the managed loopback
// OpenSERP and an owner-declared private SearXNG.
func (c *webClient) directFetch(ctx context.Context, rawURL string, timeout time.Duration) ([]byte, error) {
	ctx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, rawURL, nil)
	if err != nil {
		return nil, fmt.Errorf("<urlopen error %v>", err)
	}
	req.Header.Set("User-Agent", c.userAgent())
	req.Header.Set("Accept", "application/json")
	resp, err := (&http.Client{Transport: &http.Transport{Proxy: http.ProxyFromEnvironment}}).Do(req)
	if err != nil {
		return nil, fmt.Errorf("<urlopen error %v>", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode >= 400 {
		return nil, webHTTPError(resp.StatusCode)
	}
	return io.ReadAll(io.LimitReader(resp.Body, 2_000_000))
}
