package tools

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/url"
	"regexp"
	"strings"
	"sync"
	"time"
)

// Search backends (web_helpers.py run_search). Order: the owner's own SearXNG
// when web_search_url is set, then the managed OpenSERP on loopback, then
// DuckDuckGo's no-JavaScript results page. docs/WEB_ETIQUETTE.md has the why.

const (
	webDDGEndpoint      = "https://html.duckduckgo.com/html/"
	webOpenSERPBase     = "http://127.0.0.1:17410"
	webDDGBlockCooldown = 600 * time.Second
)

// webOpenSERPEngines: Ecosia refuses automated queries; Google's terms forbid
// them, so it is left to the owner.
var webOpenSERPEngines = []string{"duckduckgo", "bing"}

const webSearchBlockedNote = "Web search is unavailable right now: DuckDuckGo answered with a " +
	"'prove you are human' check, and Remedy never solves those. Do not " +
	"retry web search, and do not fetch search engines or review sites " +
	"(Google, Bing, Yelp) instead — they refuse automated reads too. Answer " +
	"from what you already know and say it is unverified, fetch a specific " +
	"page you know the address of, or ask the owner to look it up in the " +
	"Browser."

// errSearchBlocked is SearchBlockedError: the service served a human check.
var errSearchBlocked = errors.New(webSearchBlockedNote)

var (
	webDDGResultA    = regexp.MustCompile(`(?is)class="[^"]*result__a[^"]*"[^>]*href="([^"]+)"[^>]*>(.*?)</a>`)
	webDDGResultSnip = regexp.MustCompile(`(?is)class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</(?:a|td|div)>`)
)

var (
	webDDGBlockMu    sync.Mutex
	webDDGBlockUntil time.Time
)

func ddgBlockedNow() bool {
	webDDGBlockMu.Lock()
	defer webDDGBlockMu.Unlock()
	return time.Now().Before(webDDGBlockUntil)
}

func ddgMarkBlocked() {
	webDDGBlockMu.Lock()
	webDDGBlockUntil = time.Now().Add(webDDGBlockCooldown)
	webDDGBlockMu.Unlock()
}

type searchRow struct {
	Title   string `json:"title"`
	URL     string `json:"url"`
	Snippet string `json:"snippet,omitempty"`
}

// ddgChallengePage reports a DuckDuckGo bot challenge instead of results.
func ddgChallengePage(page string) bool {
	low := strings.ToLower(page)
	if low == "" {
		return false
	}
	if strings.Contains(low, `class="result__a`) || strings.Contains(low, `class='result__a`) {
		return false
	}
	return strings.Contains(low, "anomaly-modal") ||
		strings.Contains(low, "bots use duckduckgo too") ||
		strings.Contains(low, "confirm this search was made by a human")
}

// unwrapDDGHref turns /l/?uddg=<target> back into the target.
func unwrapDDGHref(href string) string {
	h := strings.TrimSpace(href)
	if h == "" {
		return ""
	}
	if strings.HasPrefix(h, "//") {
		h = "https:" + h
	}
	if strings.HasPrefix(h, "/") {
		if base, err := url.Parse("https://html.duckduckgo.com"); err == nil {
			if ref, err := url.Parse(h); err == nil {
				h = base.ResolveReference(ref).String()
			}
		}
	}
	query := h
	if i := strings.Index(query, "#"); i >= 0 {
		query = query[:i]
	}
	if i := strings.Index(query, "?"); i >= 0 {
		values, _ := url.ParseQuery(query[i+1:])
		if v := values["uddg"]; len(v) > 0 && v[0] != "" {
			return pyUnquote(v[0])
		}
	}
	return h
}

func truncRunes(s string, n int) string {
	if r := []rune(s); len(r) > n {
		return string(r[:n])
	}
	return s
}

// parseDDGHTMLResults is parse_ddg_html_results.
func parseDDGHTMLResults(page string, maxResults int) []searchRow {
	limit := maxResults
	if limit > 10 {
		limit = 10
	}
	if limit < 1 {
		limit = 1
	}
	var snips []string
	for _, m := range webDDGResultSnip.FindAllStringSubmatch(page, -1) {
		snips = append(snips, stripTags(m[1]))
	}
	var results []searchRow
	seen := map[string]bool{}
	for i, m := range webDDGResultA.FindAllStringSubmatch(page, -1) {
		if len(results) >= limit {
			break
		}
		target := unwrapDDGHref(m[1])
		title := stripTags(m[2])
		if target == "" || title == "" {
			continue
		}
		if !strings.HasPrefix(target, "http://") && !strings.HasPrefix(target, "https://") {
			continue
		}
		low := strings.ToLower(target)
		if strings.Contains(low, "duckduckgo.com") && !strings.Contains(low, "/l/?") {
			if strings.Contains(low, "/y.js") || strings.Contains(low, "duckduckgo.com/html") {
				continue
			}
		}
		if seen[target] {
			continue
		}
		seen[target] = true
		snippet := ""
		if i < len(snips) {
			snippet = snips[i]
		}
		results = append(results, searchRow{
			Title: truncRunes(title, 200), URL: truncRunes(target, 500), Snippet: truncRunes(snippet, 400),
		})
	}
	return results
}

func (c *webClient) ddgRows(ctx context.Context, q string, n int, timeout time.Duration) ([]searchRow, error) {
	if ddgBlockedNow() {
		return nil, errSearchBlocked // inside the cooldown: no network at all
	}
	endpoint := c.ddgEndpoint
	if endpoint == "" {
		endpoint = webDDGEndpoint
	}
	searchURL := endpoint + "?" + url.Values{"q": {q}}.Encode()
	// Paced, but not robots-gated: their robots.txt allows every agent.
	_, raw, charset, err := c.politeFetch(ctx, searchURL, 200_000, timeout, false)
	if err != nil {
		return nil, err
	}
	page := decodeCharset(raw, charset)
	if ddgChallengePage(page) {
		ddgMarkBlocked()
		return nil, errSearchBlocked
	}
	return parseDDGHTMLResults(page, n), nil
}

// pyStr is str(v or "") for a JSON value.
func pyStr(v any) string {
	switch t := v.(type) {
	case nil:
		return ""
	case string:
		return t
	case bool:
		if !t {
			return ""
		}
		return "True"
	case float64:
		if t == 0 {
			return ""
		}
		return fmt.Sprint(t)
	default:
		b, _ := json.Marshal(t)
		return string(b)
	}
}

type webJSONResults struct {
	Results []map[string]any `json:"results"`
}

func (c *webClient) searxngBase() string {
	base := strings.TrimRight(strings.TrimSpace(cfgStringKey(c.cfg(), "web_search_url")), "/")
	if strings.HasPrefix(base, "http://") || strings.HasPrefix(base, "https://") {
		return base
	}
	return ""
}

func (c *webClient) searxngRows(ctx context.Context, base, q string, n int, timeout time.Duration) ([]searchRow, error) {
	searchURL := base + "/search?q=" + url.QueryEscape(q) + "&format=json"
	host := ""
	if u, err := url.Parse(base); err == nil {
		host = strings.ToLower(u.Hostname())
	}
	var text string
	// A self-hosted instance usually lives on loopback or the LAN, which the
	// SSRF guard refuses; the owner opens that by hand in config.toml.
	if c.hostBlocked(ctx, host) {
		if !cfgBoolKey(c.cfg(), "web_search_url_allow_private", false) {
			return nil, fmt.Errorf("SEARCH_PRIVATE_HOST %s is a private/loopback address; set "+
				"web_search_url_allow_private=true to allow your own instance", host)
		}
		raw, err := c.directFetch(ctx, searchURL, timeout)
		if err != nil {
			return nil, err
		}
		text = decodeReplace(raw)
	} else {
		_, raw, charset, err := c.politeFetch(ctx, searchURL, 400_000, timeout, false)
		if err != nil {
			return nil, err
		}
		text = decodeCharset(raw, charset)
	}
	var data webJSONResults
	if err := json.Unmarshal([]byte(text), &data); err != nil {
		return nil, fmt.Errorf("SEARCH_BACKEND %s did not return JSON — add 'json' to "+
			"search.formats in its settings.yml (%v)", base, err)
	}
	items := data.Results
	if n < 1 {
		n = 1
	}
	if len(items) > n {
		items = items[:n]
	}
	var rows []searchRow
	for _, item := range items {
		target := strings.TrimSpace(pyStr(item["url"]))
		if !strings.HasPrefix(target, "http://") && !strings.HasPrefix(target, "https://") {
			continue
		}
		title := stripTags(pyStr(item["title"]))
		if title == "" {
			title = target
		}
		rows = append(rows, searchRow{Title: title, URL: target, Snippet: truncRunes(stripTags(pyStr(item["content"])), 300)})
	}
	return rows, nil
}

// openserpHealthy is web_search_host.is_healthy: GET /health within 0.6 s.
func (c *webClient) openserpHealthy(ctx context.Context) bool {
	if c.openserpBase == "" {
		return false
	}
	_, err := c.directFetch(ctx, c.openserpBase+"/health", 600*time.Millisecond)
	return err == nil
}

// openserpRows queries the managed loopback OpenSERP. Starting or downloading
// it is not done here: the Python worker still owns that (_start_local_search).
func (c *webClient) openserpRows(ctx context.Context, q string, n int, timeout time.Duration) ([]searchRow, error) {
	if !c.openserpHealthy(ctx) {
		return nil, errors.New("openserp not healthy")
	}
	base := strings.TrimRight(c.openserpBase, "/")
	if n < 1 {
		n = 1
	}
	var lastErr error
	for _, engine := range webOpenSERPEngines {
		searchURL := fmt.Sprintf("%s/%s/search?text=%s&limit=%d", base, engine, url.QueryEscape(q), n)
		raw, err := c.directFetch(ctx, searchURL, timeout)
		if err != nil {
			lastErr = err
			continue
		}
		var data webJSONResults
		if err := json.Unmarshal([]byte(decodeReplace(raw)), &data); err != nil {
			lastErr = err
			continue
		}
		var rows []searchRow
		for _, item := range data.Results {
			kind := pyStr(item["type"])
			if kind == "" {
				kind = "organic"
			}
			if kind != "organic" {
				continue
			}
			target := strings.TrimSpace(pyStr(item["url"]))
			if !strings.HasPrefix(target, "http://") && !strings.HasPrefix(target, "https://") {
				continue
			}
			title := stripTags(pyStr(item["title"]))
			if title == "" {
				title = target
			}
			rows = append(rows, searchRow{Title: title, URL: target, Snippet: truncRunes(stripTags(pyStr(item["snippet"])), 300)})
			if len(rows) >= n {
				break
			}
		}
		if len(rows) > 0 {
			return rows, nil
		}
	}
	return nil, lastErr
}

// runSearch returns (rows, backend name).
func (c *webClient) runSearch(ctx context.Context, q string, n int, timeout time.Duration) ([]searchRow, string, error) {
	if base := c.searxngBase(); base != "" {
		rows, err := c.searxngRows(ctx, base, q, n, timeout)
		return rows, "your search instance", err
	}
	if rows, err := c.openserpRows(ctx, q, n, timeout); err == nil && len(rows) > 0 {
		return rows, "OpenSERP (local)", nil
	}
	rows, err := c.ddgRows(ctx, q, n, timeout)
	return rows, "DuckDuckGo HTML", err
}
