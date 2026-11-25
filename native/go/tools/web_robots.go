package tools

import (
	"context"
	"fmt"
	"net/url"
	"strconv"
	"strings"
	"sync"
	"time"
	"unicode/utf8"
)

// Politeness: robots.txt and per-host pacing (web_helpers.py). robots.txt is
// read and obeyed by default; web_respect_robots = false turns the gate off.
// Pacing and the self-identifying User-Agent stay on either way.

const (
	webRobotsAgent    = "RemedyAI-WebFetch"
	webRobotsTTL      = time.Hour
	webRobotsMaxHosts = 512
	webRobotsBytes    = 200_000
	// webMaxPoliteWait: a longer Crawl-delay is refused, not slept through.
	webMaxPoliteWait = 10 * time.Second
	webDefaultDelay  = time.Second
)

// Process-wide like the Python module globals, so two registries in one
// process still share one pace per host.
var (
	webRobotsMu    sync.Mutex
	webRobotsCache = map[string]robotsCacheEntry{}
	webPaceMu      sync.Mutex
	webLastFetchAt = map[string]time.Time{}
)

type robotsCacheEntry struct {
	at     time.Time
	parser *robotsParser // nil: no usable rule
}

// robotsParser is a port of urllib.robotparser.RobotFileParser, including its
// first-match (not longest-match) rule order and substring agent matching.
type robotsParser struct {
	entries      []robotsEntry
	defaultEntry *robotsEntry
}

type robotsEntry struct {
	agents []string
	rules  []robotsRule
	delay  *int
}

type robotsRule struct {
	path  string
	allow bool
}

func newRobotsRule(path string, allow bool) robotsRule {
	if path == "" && !allow {
		allow = true
	}
	return robotsRule{path: pyQuote(path), allow: allow}
}

func (r robotsRule) appliesTo(filename string) bool {
	return r.path == "*" || strings.HasPrefix(filename, r.path)
}

func (e *robotsEntry) appliesTo(useragent string) bool {
	useragent = strings.ToLower(strings.SplitN(useragent, "/", 2)[0])
	for _, agent := range e.agents {
		if agent == "*" {
			return true
		}
		if strings.Contains(useragent, strings.ToLower(agent)) {
			return true
		}
	}
	return false
}

func (e *robotsEntry) allowance(filename string) bool {
	for _, r := range e.rules {
		if r.appliesTo(filename) {
			return r.allow
		}
	}
	return true
}

func parseRobots(text string) *robotsParser {
	p := &robotsParser{}
	add := func(e robotsEntry) {
		for _, a := range e.agents {
			if a == "*" {
				if p.defaultEntry == nil {
					ec := e
					p.defaultEntry = &ec
				}
				return
			}
		}
		p.entries = append(p.entries, e)
	}
	text = strings.ReplaceAll(strings.ReplaceAll(text, "\r\n", "\n"), "\r", "\n")
	state := 0
	entry := robotsEntry{}
	for _, line := range strings.Split(text, "\n") {
		if line == "" {
			if state == 1 {
				entry, state = robotsEntry{}, 0
			} else if state == 2 {
				add(entry)
				entry, state = robotsEntry{}, 0
			}
		}
		if i := strings.Index(line, "#"); i >= 0 {
			line = line[:i]
		}
		line = strings.TrimSpace(line)
		if line == "" {
			continue
		}
		key, val, ok := strings.Cut(line, ":")
		if !ok {
			continue
		}
		key = strings.ToLower(strings.TrimSpace(key))
		val = pyUnquote(strings.TrimSpace(val))
		switch key {
		case "user-agent":
			if state == 2 {
				add(entry)
				entry = robotsEntry{}
			}
			entry.agents = append(entry.agents, val)
			state = 1
		case "disallow", "allow":
			if state != 0 {
				entry.rules = append(entry.rules, newRobotsRule(val, key == "allow"))
				state = 2
			}
		case "crawl-delay":
			if state != 0 {
				if d := strings.TrimSpace(val); d != "" && isASCIIDigits(d) {
					if n, err := strconv.Atoi(d); err == nil {
						entry.delay = &n
					}
				}
				state = 2
			}
		case "request-rate":
			if state != 0 {
				state = 2
			}
		}
	}
	if state == 2 {
		add(entry)
	}
	return p
}

func isASCIIDigits(s string) bool {
	for i := 0; i < len(s); i++ {
		if s[i] < '0' || s[i] > '9' {
			return false
		}
	}
	return true
}

// robotsFilename normalizes a URL the way can_fetch does: unquote, keep
// path;params?query#fragment, quote again.
func robotsFilename(rawURL string) string {
	s := pyUnquote(rawURL)
	if i := strings.Index(s, "://"); i >= 0 {
		rest := s[i+3:]
		if j := strings.IndexAny(rest, "/?#"); j >= 0 {
			s = rest[j:]
		} else {
			s = ""
		}
	}
	s = pyQuote(s)
	if s == "" {
		s = "/"
	}
	return s
}

func (p *robotsParser) canFetch(useragent, rawURL string) bool {
	filename := robotsFilename(rawURL)
	for i := range p.entries {
		if p.entries[i].appliesTo(useragent) {
			return p.entries[i].allowance(filename)
		}
	}
	if p.defaultEntry != nil {
		return p.defaultEntry.allowance(filename)
	}
	return true
}

func (p *robotsParser) crawlDelay(useragent string) *int {
	for i := range p.entries {
		if p.entries[i].appliesTo(useragent) {
			return p.entries[i].delay
		}
	}
	if p.defaultEntry != nil {
		return p.defaultEntry.delay
	}
	return nil
}

// pyQuote is urllib.parse.quote(s, safe="/").
func pyQuote(s string) string {
	var b strings.Builder
	for i := 0; i < len(s); i++ {
		c := s[i]
		if (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') ||
			c == '_' || c == '.' || c == '-' || c == '~' || c == '/' {
			b.WriteByte(c)
		} else {
			fmt.Fprintf(&b, "%%%02X", c)
		}
	}
	return b.String()
}

// pyUnquote is urllib.parse.unquote: %XX decoded, malformed escapes kept,
// invalid UTF-8 replaced.
func pyUnquote(s string) string {
	if !strings.Contains(s, "%") {
		return s
	}
	buf := make([]byte, 0, len(s))
	for i := 0; i < len(s); i++ {
		if s[i] == '%' && i+2 < len(s) {
			if v, err := strconv.ParseUint(s[i+1:i+3], 16, 8); err == nil {
				buf = append(buf, byte(v))
				i += 2
				continue
			}
		}
		buf = append(buf, s[i])
	}
	return decodeReplace(buf)
}

// decodeReplace decodes UTF-8 with one U+FFFD per invalid byte.
func decodeReplace(b []byte) string {
	if utf8.Valid(b) {
		return string(b)
	}
	var sb strings.Builder
	for len(b) > 0 {
		r, size := utf8.DecodeRune(b)
		if r == utf8.RuneError && size == 1 {
			sb.WriteRune(utf8.RuneError)
		} else {
			sb.Write(b[:size])
		}
		b = b[size:]
	}
	return sb.String()
}

func robotsKey(u *url.URL) string {
	scheme := strings.ToLower(u.Scheme)
	if scheme == "" {
		scheme = "https"
	}
	host := strings.ToLower(u.Hostname())
	if p := u.Port(); p != "" {
		return scheme + "://" + host + ":" + p
	}
	return scheme + "://" + host
}

// loadRobots fetches origin/robots.txt. Fail open: no file, a 4xx, a timeout
// or junk means no stated rule. A served 200 is obeyed exactly.
func (c *webClient) loadRobots(ctx context.Context, origin string) *robotsParser {
	now := time.Now()
	webRobotsMu.Lock()
	if hit, ok := webRobotsCache[origin]; ok && now.Sub(hit.at) < webRobotsTTL {
		webRobotsMu.Unlock()
		return hit.parser
	}
	webRobotsMu.Unlock()

	var parser *robotsParser
	if _, raw, charset, err := c.pinnedFetch(ctx, origin+"/robots.txt", webRobotsBytes, 8*time.Second); err == nil {
		parser = parseRobots(decodeCharset(raw, charset))
	}

	webRobotsMu.Lock()
	if len(webRobotsCache) >= webRobotsMaxHosts {
		webRobotsCache = map[string]robotsCacheEntry{}
	}
	webRobotsCache[origin] = robotsCacheEntry{at: now, parser: parser}
	webRobotsMu.Unlock()
	return parser
}

// robotsGate errors with ROBOTS_BLOCKED when robots.txt says no, and returns
// the crawl delay the host asks for (0 when unstated).
func (c *webClient) robotsGate(ctx context.Context, rawURL string) (time.Duration, error) {
	u, err := url.Parse(rawURL)
	if err != nil {
		return 0, nil
	}
	origin := robotsKey(u)
	parser := c.loadRobots(ctx, origin)
	if parser == nil {
		return 0, nil
	}
	if !parser.canFetch(webRobotsAgent, rawURL) {
		name := u.Hostname()
		if name == "" {
			name = origin
		}
		return 0, fmt.Errorf("ROBOTS_BLOCKED %s", name)
	}
	if d := parser.crawlDelay(webRobotsAgent); d != nil {
		return time.Duration(*d) * time.Second, nil
	}
	return 0, nil
}

// paceHost sleeps just long enough that two hits on host are not back to back.
func (c *webClient) paceHost(ctx context.Context, host string, crawlDelay time.Duration) error {
	waitFor := c.minDelay
	if crawlDelay > waitFor {
		waitFor = crawlDelay
	}
	webPaceMu.Lock()
	now := time.Now()
	var remaining time.Duration
	if last, ok := webLastFetchAt[host]; ok {
		remaining = waitFor - now.Sub(last)
	}
	if remaining > webMaxPoliteWait {
		webPaceMu.Unlock()
		return fmt.Errorf("ROBOTS_DELAY %s asks for %.0fs between requests", host, waitFor.Seconds())
	}
	if remaining < 0 {
		remaining = 0
	}
	// Claim the slot before unlocking so parallel fetches of one host queue.
	webLastFetchAt[host] = now.Add(remaining)
	if len(webLastFetchAt) > webRobotsMaxHosts {
		dropped := 0
		for k, v := range webLastFetchAt {
			if dropped >= 256 {
				break
			}
			if now.Sub(v) > 600*time.Second {
				delete(webLastFetchAt, k)
				dropped++
			}
		}
	}
	webPaceMu.Unlock()
	if remaining > 0 {
		t := time.NewTimer(remaining)
		defer t.Stop()
		select {
		case <-t.C:
		case <-ctx.Done():
			return ctx.Err()
		}
	}
	return nil
}

func (c *webClient) robotsRespected() bool {
	return cfgBoolKey(c.cfg(), "web_respect_robots", true)
}

// politeFetch is pinnedFetch with robots.txt honoured and per-host pacing.
func (c *webClient) politeFetch(ctx context.Context, rawURL string, maxBytes int, timeout time.Duration, respectRobots bool) (string, []byte, string, error) {
	rawURL = strings.TrimSpace(rawURL)
	host := ""
	if u, err := url.Parse(rawURL); err == nil {
		host = strings.ToLower(u.Hostname())
	}
	var delay time.Duration
	if respectRobots {
		d, err := c.robotsGate(ctx, rawURL)
		if err != nil {
			return "", nil, "", err
		}
		delay = d
	}
	if host != "" {
		if err := c.paceHost(ctx, host, delay); err != nil {
			return "", nil, "", err
		}
	}
	finalURL, raw, charset, err := c.pinnedFetch(ctx, rawURL, maxBytes, timeout)
	if err != nil {
		return "", nil, "", err
	}
	// A redirect can land on a host whose robots.txt was never consulted.
	if respectRobots {
		if fu, err := url.Parse(finalURL); err == nil {
			if fh := strings.ToLower(fu.Hostname()); fh != "" && fh != host {
				if _, err := c.robotsGate(ctx, finalURL); err != nil {
					return "", nil, "", err
				}
			}
		}
	}
	return finalURL, raw, charset, nil
}
