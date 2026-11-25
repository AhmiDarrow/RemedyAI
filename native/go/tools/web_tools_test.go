package tools

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"net/netip"
	"net/url"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

func resetWebState() {
	webRobotsMu.Lock()
	webRobotsCache = map[string]robotsCacheEntry{}
	webRobotsMu.Unlock()
	webPaceMu.Lock()
	webLastFetchAt = map[string]time.Time{}
	webPaceMu.Unlock()
	webDDGBlockMu.Lock()
	webDDGBlockUntil = time.Time{}
	webDDGBlockMu.Unlock()
}

var loopback = netip.MustParseAddr("127.0.0.1")

// testWebClient resolves *.test names: private.test to 10.0.0.5, anything
// else to loopback. Loopback is admitted only through allowAddr, the test seam.
func testWebClient(t *testing.T, cfg map[string]any) *webClient {
	t.Helper()
	resetWebState()
	t.Cleanup(resetWebState)
	return &webClient{
		version: "9.9.9",
		config:  func() map[string]any { return cfg },
		lookup: func(_ context.Context, host string) ([]netip.Addr, error) {
			if host == "private.test" {
				return []netip.Addr{netip.MustParseAddr("10.0.0.5")}, nil
			}
			if host == "mixed.test" {
				return []netip.Addr{netip.MustParseAddr("93.184.216.34"), netip.MustParseAddr("192.168.1.1")}, nil
			}
			if strings.HasSuffix(host, ".test") {
				return []netip.Addr{loopback}, nil
			}
			return nil, fmt.Errorf("no network in tests: %s", host)
		},
		allowAddr: func(ip netip.Addr) bool { return ip == loopback },
	}
}

// siteURL rewrites an httptest URL to name.test:port so it passes the guard.
func siteURL(t *testing.T, srv *httptest.Server, name, path string) string {
	t.Helper()
	u, err := url.Parse(srv.URL)
	if err != nil {
		t.Fatal(err)
	}
	return "http://" + name + ":" + u.Port() + path
}

func TestWebIPBlocked(t *testing.T) {
	cases := []struct {
		ip      string
		blocked bool
	}{
		{"0.0.0.0", true}, {"10.1.2.3", true}, {"100.64.0.1", true}, {"127.0.0.1", true},
		{"169.254.169.254", true}, {"172.16.0.1", true}, {"172.31.255.255", true},
		{"192.0.0.8", true}, {"192.0.2.1", true}, {"192.168.0.1", true}, {"198.18.0.1", true},
		{"198.51.100.1", true}, {"203.0.113.9", true}, {"224.0.0.1", true}, {"240.0.0.1", true},
		{"255.255.255.255", true},
		{"::", true}, {"::1", true}, {"fe80::1", true}, {"fc00::1", true}, {"fd12:3456::1", true},
		{"ff02::1", true}, {"2001:db8::1", true}, {"fec0::1", true}, {"2002:a00:1::1", true},
		{"::ffff:127.0.0.1", true}, {"::ffff:10.0.0.1", true}, {"::ffff:169.254.169.254", true},
		{"64:ff9b::a00:1", true}, {"100::1", true},
		{"8.8.8.8", false}, {"93.184.216.34", false}, {"172.32.0.1", false},
		{"2606:4700:4700::1111", false}, {"::ffff:8.8.8.8", false}, {"64:ff9b::808:808", false},
	}
	for _, tc := range cases {
		if got := webIPBlocked(netip.MustParseAddr(tc.ip)); got != tc.blocked {
			t.Errorf("webIPBlocked(%s)=%v want %v", tc.ip, got, tc.blocked)
		}
	}
}

func TestWebHostBlocked(t *testing.T) {
	c := testWebClient(t, nil)
	c.allowAddr = nil
	for _, h := range []string{"", "localhost", "LOCALHOST.", "metadata.google.internal", "foo.internal",
		"printer.local", "a.metadata.example", "10.0.0.1.nip.io", "x.sslip.io", "169.254.1.1",
		"127.0.0.1", "[::1]", "private.test", "mixed.test", "site.test", "unresolvable.example"} {
		if !c.hostBlocked(context.Background(), strings.Trim(h, "[]")) {
			t.Errorf("host %q must be blocked", h)
		}
	}
	if c.hostBlocked(context.Background(), "8.8.8.8") {
		t.Error("public literal must be allowed")
	}
}

func TestWebFetchLoopbackRefusedByDefault(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		t.Error("loopback server must never be reached")
	}))
	defer srv.Close()
	// Production configuration: no allowAddr seam.
	reg := NewRegistry()
	if err := RegisterWebTools(reg, WebOptions{Version: "1"}); err != nil {
		t.Fatal(err)
	}
	c := &webClient{ddgEndpoint: webDDGEndpoint, minDelay: webDefaultDelay}
	_, _, _, err := c.pinnedFetch(context.Background(), srv.URL+"/", 1000, 2*time.Second)
	if err == nil || err.Error() != "SSRF_BLOCKED" {
		t.Fatalf("err=%v", err)
	}
}

func TestWebRedirectGuard(t *testing.T) {
	var hits atomic.Int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		hits.Add(1)
		switch r.URL.Path {
		case "/to-private":
			http.Redirect(w, r, "http://private.test/secret", http.StatusFound)
		case "/to-metadata":
			http.Redirect(w, r, "http://169.254.169.254/latest/meta-data/", http.StatusMovedPermanently)
		case "/to-userinfo":
			http.Redirect(w, r, "http://user:pw@site.test/", http.StatusFound)
		case "/to-ftp":
			http.Redirect(w, r, "ftp://site.test/", http.StatusFound)
		case "/no-location":
			w.WriteHeader(http.StatusFound)
		case "/hop":
			http.Redirect(w, r, "/final", http.StatusTemporaryRedirect)
		case "/final":
			if r.Host == "" || !strings.HasPrefix(r.Host, "site.test:") {
				t.Errorf("Host header %q must keep the name, not the pinned IP", r.Host)
			}
			if !strings.HasPrefix(r.UserAgent(), "RemedyAI-WebFetch/9.9.9 ") {
				t.Errorf("UA=%q", r.UserAgent())
			}
			_, _ = w.Write([]byte("done"))
		case "/loop":
			http.Redirect(w, r, "/loop", http.StatusFound)
		}
	}))
	defer srv.Close()
	c := testWebClient(t, nil)
	cases := []struct{ path, want string }{
		{"/to-private", "SSRF_BLOCKED_REDIRECT"},
		{"/to-metadata", "SSRF_BLOCKED_REDIRECT"},
		{"/to-userinfo", "URL_USERINFO_BLOCKED"},
		{"/to-ftp", "url must start with http:// or https://"},
		{"/no-location", "HTTP 302 redirect without Location"},
	}
	for _, tc := range cases {
		_, _, _, err := c.pinnedFetch(context.Background(), siteURL(t, srv, "site.test", tc.path), 1000, 2*time.Second)
		if err == nil || err.Error() != tc.want {
			t.Errorf("%s: err=%v want %s", tc.path, err, tc.want)
		}
	}
	final, body, _, err := c.pinnedFetch(context.Background(), siteURL(t, srv, "site.test", "/hop"), 1000, 2*time.Second)
	if err != nil || string(body) != "done" || !strings.HasSuffix(final, "/final") {
		t.Fatalf("hop: final=%q body=%q err=%v", final, body, err)
	}

	hits.Store(0)
	_, _, _, err = c.pinnedFetch(context.Background(), siteURL(t, srv, "site.test", "/loop"), 1000, 2*time.Second)
	if err == nil || err.Error() != "Too many redirects" {
		t.Fatalf("loop err=%v", err)
	}
	if n := hits.Load(); n != webMaxRedirects+1 {
		t.Fatalf("loop made %d requests, want %d", n, webMaxRedirects+1)
	}
	// userinfo on the first URL and a private first hop are refused too.
	if _, _, _, err := c.pinnedFetch(context.Background(), "http://a:b@site.test/", 10, time.Second); err == nil || err.Error() != "URL_USERINFO_BLOCKED" {
		t.Fatalf("userinfo err=%v", err)
	}
	if _, _, _, err := c.pinnedFetch(context.Background(), "http://mixed.test/", 10, time.Second); err == nil || err.Error() != "SSRF_BLOCKED" {
		t.Fatalf("mixed err=%v", err)
	}
}

func TestWebByteCapAndStatus(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/missing" {
			http.NotFound(w, r)
			return
		}
		w.Header().Set("Content-Type", "text/plain; Charset=ISO-8859-1")
		_, _ = w.Write([]byte(strings.Repeat("x", 1<<20)))
	}))
	defer srv.Close()
	c := testWebClient(t, nil)
	_, body, charset, err := c.pinnedFetch(context.Background(), siteURL(t, srv, "site.test", "/big"), 1000, 2*time.Second)
	if err != nil {
		t.Fatal(err)
	}
	if len(body) != 1001 || charset != "ISO-8859-1" {
		t.Fatalf("len=%d charset=%q", len(body), charset)
	}
	if _, _, _, err := c.pinnedFetch(context.Background(), siteURL(t, srv, "site.test", "/missing"), 1000, 2*time.Second); err == nil || err.Error() != "HTTP Error 404: Not Found" {
		t.Fatalf("404 err=%v", err)
	}
}

func TestWebRobots(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/robots.txt" {
			_, _ = w.Write([]byte("User-agent: *\nDisallow: /private\n\nUser-agent: RemedyAI-WebFetch\nAllow: /open/x\nDisallow: /open\n"))
			return
		}
		_, _ = w.Write([]byte("page " + r.URL.Path))
	}))
	defer srv.Close()

	for _, tc := range []struct {
		respect bool
		path    string
		blocked bool
	}{
		{true, "/open/x", false}, // first matching rule wins
		{true, "/open/y", true},
		{true, "/private", false}, // our own group applies, not '*'
		{true, "/other", false},
		{false, "/open/y", false},
	} {
		c := testWebClient(t, map[string]any{"web_respect_robots": tc.respect})
		_, _, _, err := c.politeFetch(context.Background(), siteURL(t, srv, "site.test", tc.path), 1000, 2*time.Second, c.robotsRespected())
		if tc.blocked {
			if err == nil || !strings.HasPrefix(err.Error(), "ROBOTS_BLOCKED site.test") {
				t.Errorf("respect=%v %s: err=%v", tc.respect, tc.path, err)
			}
		} else if err != nil {
			t.Errorf("respect=%v %s: err=%v", tc.respect, tc.path, err)
		}
	}
}

func TestRobotsParser(t *testing.T) {
	p := parseRobots("# c\nUser-agent: other\nDisallow: /\n\nUser-agent: *\nDisallow: /a%20b\nDisallow:\nCrawl-delay: 3\n")
	if p.canFetch("RemedyAI-WebFetch", "https://x/a b/c") {
		t.Error("percent-normalized path must match")
	}
	if !p.canFetch("RemedyAI-WebFetch", "https://x/z") {
		t.Error("unlisted path allowed")
	}
	if d := p.crawlDelay(webRobotsAgent); d == nil || *d != 3 {
		t.Errorf("crawl delay=%v", d)
	}
	if p.canFetch("Other/1.0", "https://x/z") {
		t.Error("other agent is disallowed everywhere")
	}
}

func TestWebPaceRefusesLongCrawlDelay(t *testing.T) {
	c := testWebClient(t, nil)
	if err := c.paceHost(context.Background(), "slow.test", 100*time.Second); err != nil {
		t.Fatal(err)
	}
	err := c.paceHost(context.Background(), "slow.test", 100*time.Second)
	if err == nil || err.Error() != "ROBOTS_DELAY slow.test asks for 100s between requests" {
		t.Fatalf("err=%v", err)
	}
}

const ddgFixture = `<html><body>
<div class="result"><a rel="nofollow" class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fa%3Fx%3D1&amp;rut=abc">Example &amp; <b>One</b></a>
<a class="result__snippet" href="#">First <b>snippet</b>  here</a></div>
<div class="result"><a class="result__a" href="https://example.org/two">Two</a>
<div class="result__snippet">Second</div></div>
<div class="result"><a class="result__a" href="https://example.org/two">Two again</a></div>
<div class="result"><a class="result__a" href="https://duckduckgo.com/y.js?ad=1">Ad</a></div>
<div class="result"><a class="result__a" href="javascript:void(0)">Bad</a></div>
</body></html>`

func TestParseDDGHTMLResults(t *testing.T) {
	rows := parseDDGHTMLResults(ddgFixture, 5)
	want := []searchRow{
		{Title: "Example & One", URL: "https://example.com/a?x=1", Snippet: "First snippet here"},
		{Title: "Two", URL: "https://example.org/two", Snippet: "Second"},
	}
	if len(rows) != len(want) {
		t.Fatalf("rows=%+v", rows)
	}
	for i := range want {
		if rows[i] != want[i] {
			t.Errorf("row %d=%+v want %+v", i, rows[i], want[i])
		}
	}
	if got := parseDDGHTMLResults(ddgFixture, 1); len(got) != 1 {
		t.Fatalf("limit: %d rows", len(got))
	}
	if ddgChallengePage(ddgFixture) {
		t.Fatal("results page is not a challenge")
	}
}

func newWebRegistry(t *testing.T, c *webClient) *Registry {
	t.Helper()
	reg := NewRegistry(AuthorizerFunc(func(context.Context, Descriptor, Request) error { return nil }))
	if err := registerWebTools(reg, c); err != nil {
		t.Fatal(err)
	}
	return reg
}

func TestWebSearchChallengeBlocks(t *testing.T) {
	var hits atomic.Int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		hits.Add(1)
		if r.URL.Query().Get("q") != "remedy test" {
			t.Errorf("q=%q", r.URL.Query().Get("q"))
		}
		_, _ = w.Write([]byte(`<html><div class="anomaly-modal">Unfortunately, bots use DuckDuckGo too.</div></html>`))
	}))
	defer srv.Close()
	c := testWebClient(t, nil)
	c.ddgEndpoint = siteURL(t, srv, "ddg.test", "/html/")
	reg := newWebRegistry(t, c)

	for round := 0; round < 2; round++ {
		res, err := reg.Execute(context.Background(), Request{ToolID: "web.search", Version: 1, Input: json.RawMessage(`{"query":"  remedy test "}`)})
		if err != nil {
			t.Fatalf("round %d: %v", round, err)
		}
		var out struct {
			Query, Backend, Note string
			Results              []searchRow
			Blocked              bool
		}
		if err := json.Unmarshal(res.Output, &out); err != nil {
			t.Fatal(err)
		}
		if !out.Blocked || out.Note != webSearchBlockedNote || out.Backend != "DuckDuckGo HTML" || out.Results == nil || len(out.Results) != 0 || out.Query != "remedy test" {
			t.Fatalf("round %d: %s", round, res.Output)
		}
	}
	if n := hits.Load(); n != 1 {
		t.Fatalf("cooldown must skip the network: %d hits", n)
	}
}

func TestWebSearchResults(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = w.Write([]byte(ddgFixture))
	}))
	defer srv.Close()
	c := testWebClient(t, nil)
	c.ddgEndpoint = siteURL(t, srv, "ddg.test", "/html/")
	reg := newWebRegistry(t, c)
	res, err := reg.Execute(context.Background(), Request{ToolID: "web.search", Version: 1, Input: json.RawMessage(`{"query":"x","max_results":1}`)})
	if err != nil {
		t.Fatal(err)
	}
	var out struct {
		Query, Backend string
		Results        []searchRow
	}
	if err := json.Unmarshal(res.Output, &out); err != nil {
		t.Fatal(err)
	}
	want := searchRow{Title: "Example & One", URL: "https://example.com/a?x=1", Snippet: "First snippet here"}
	if out.Query != "x" || out.Backend != "DuckDuckGo HTML" || len(out.Results) != 1 || out.Results[0] != want {
		t.Fatalf("out=%s", res.Output)
	}
}

func TestWebSearXNGPrivateNeedsOptIn(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = w.Write([]byte(`{"results":[{"url":"https://a.example/","title":"<b>A</b>","content":"c"},{"url":"ftp://x"}]}`))
	}))
	defer srv.Close()
	cfg := map[string]any{"web_search_url": srv.URL + "/"}
	c := testWebClient(t, cfg)
	c.allowAddr = nil // the SearXNG gate uses the real block list
	_, _, err := c.runSearch(context.Background(), "q", 5, time.Second)
	if err == nil || !strings.HasPrefix(err.Error(), "SEARCH_PRIVATE_HOST 127.0.0.1") {
		t.Fatalf("err=%v", err)
	}
	cfg["web_search_url_allow_private"] = true
	rows, backend, err := c.runSearch(context.Background(), "q", 5, time.Second)
	if err != nil || backend != "your search instance" || len(rows) != 1 || rows[0].Title != "A" {
		t.Fatalf("rows=%+v backend=%q err=%v", rows, backend, err)
	}
}

func TestWebToolErrors(t *testing.T) {
	cfg := map[string]any{}
	reg := newWebRegistry(t, testWebClient(t, cfg))
	for _, tc := range []struct{ id, in, want string }{
		{"web.search", `{"query":"   "}`, "query is required"},
		{"web.fetch", `{"url":"   "}`, "url is required"},
		{"web.fetch", `{"url":"ftp://x"}`, "url must start with http:// or https://"},
	} {
		_, err := reg.Execute(context.Background(), Request{ToolID: tc.id, Version: 1, Input: json.RawMessage(tc.in)})
		if err == nil || !strings.Contains(err.Error(), tc.want) {
			t.Errorf("%s %s: err=%v", tc.id, tc.in, err)
		}
	}
	cfg["web_tools_enabled"] = false
	for id, in := range map[string]string{"web.search": `{"query":"x"}`, "web.fetch": `{"url":"https://x.test/"}`} {
		_, err := reg.Execute(context.Background(), Request{ToolID: id, Version: 1, Input: json.RawMessage(in)})
		if err == nil || !strings.Contains(err.Error(), "web tools are disabled") {
			t.Errorf("%s disabled: err=%v", id, err)
		}
	}
}

func TestWebFetchShaping(t *testing.T) {
	page := `<!DOCTYPE html><html><head><title>Hello &amp; you</title><script>var x="<p>no</p>";</script></head>
<body><nav>menu</nav><h1>Big</h1><p>Some   <a href="/rel">link</a> and <a href="mailto:x">mail</a>.</p>
<ul><li>one</li><li>two</li></ul><pre>  keep   this</pre><p>use <code>go test</code></p></body></html>`
	c := testWebClient(t, nil)
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/page":
			_, _ = w.Write([]byte(page))
		case "/text":
			_, _ = w.Write([]byte(strings.Repeat("é", 3000)))
		case "/long":
			_, _ = w.Write([]byte("<html><body><p>" + strings.Repeat("word ", 1000) + "</p></body></html>"))
		}
	}))
	defer srv.Close()
	reg := newWebRegistry(t, c)
	fetch := func(path, extra string) map[string]any {
		t.Helper()
		in := fmt.Sprintf(`{"url":%q%s}`, siteURL(t, srv, "site.test", path), extra)
		res, err := reg.Execute(context.Background(), Request{ToolID: "web.fetch", Version: 1, Input: json.RawMessage(in)})
		if err != nil {
			t.Fatalf("%s: %v", path, err)
		}
		var out map[string]any
		_ = json.Unmarshal(res.Output, &out)
		return out
	}
	out := fetch("/page", "")
	want := "# Big\n\nSome [link](/rel) and mail.\n\n- one\n\n- two\n\n```\n  keep   this\n```\n\nuse `go test`"
	if out["format"] != "markdown" || out["title"] != "Hello & you" || out["content"] != want || out["truncated"] != nil {
		t.Fatalf("html: %#v", out)
	}
	out = fetch("/text", `,"max_chars":1000`)
	if out["format"] != "text" || len([]rune(out["content"].(string))) != 1000 || out["truncated"] != true {
		t.Fatalf("text: format=%v len=%d trunc=%v", out["format"], len([]rune(out["content"].(string))), out["truncated"])
	}
	out = fetch("/long", `,"max_chars":1000`)
	if out["format"] != "markdown" || out["truncated"] != true || !strings.HasSuffix(out["content"].(string), "…[truncated at 1000 chars]") {
		t.Fatalf("long: %#v", out)
	}
}

func TestClampMaxChars(t *testing.T) {
	p := func(v int) *int { return &v }
	for _, tc := range []struct {
		in   *int
		want int
	}{{nil, 50_000}, {p(1), 1000}, {p(5000), 5000}, {p(999_999), 200_000}} {
		if got := clampMaxChars(tc.in); got != tc.want {
			t.Errorf("clamp(%v)=%d want %d", tc.in, got, tc.want)
		}
	}
}

func TestHTMLTokenizerMalformed(t *testing.T) {
	for _, tc := range []struct{ in, want string }{
		{"<p>a < b and c > d</p>", "a < b and c > d"},
		{"<p>unclosed <b>bold", "unclosed bold"},
		{"<div class='x>broken", "<div class='x>broken"},
		{"text <!-- comment never ends", "text"},
		{"<p>x</ 3>y</p>", "xy"},
		{"<P CLASS=a>Up</P><br/>Next", "Up\n\nNext"},
		{"<script>if (a</b) {}</script>after", "after"},
		{"<p>&lt;tag&gt; &#169; &copy</p>", "<tag> © ©"},
		{"<a href=\"https://x.example\">dangling", ""},
		{"", ""},
	} {
		if got := htmlToMarkdown(tc.in, 1000).markdown; got != tc.want {
			t.Errorf("%q => %q want %q", tc.in, got, tc.want)
		}
	}
	if !htmlToMarkdown("<html><body>Please enable JavaScript to continue</body></html>", 1000).jsShell {
		t.Error("js shell hint")
	}
	if !looksLikeHTML([]byte("  <!doctype html><p>x")) || !looksLikeHTML([]byte("hi <BODY>")) || looksLikeHTML([]byte(`{"a":1}`)) {
		t.Error("looksLikeHTML")
	}
}
