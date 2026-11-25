package httpapi

import (
	"bufio"
	"context"
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/base64"
	"io"
	"math/big"
	"mime"
	"net"
	"net/http"
	"net/http/httptest"
	"net/mail"
	"strconv"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/AhmiDarrow/RemedyAI/native/go/secret"
)

const (
	testMailAddr = "user@example.com"
	testMailPass = "fake-app-password"
)

// testTLS makes a throwaway cert for 127.0.0.1 and points mailTLSConfig at it
// with verification still on (the cert is the only trusted root).
func testTLS(t *testing.T) *tls.Config {
	t.Helper()
	key, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	tpl := &x509.Certificate{
		SerialNumber: big.NewInt(1), Subject: pkix.Name{CommonName: "fake"},
		NotBefore: time.Now().Add(-time.Hour), NotAfter: time.Now().Add(time.Hour),
		KeyUsage: x509.KeyUsageDigitalSignature | x509.KeyUsageCertSign, ExtKeyUsage: []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth},
		BasicConstraintsValid: true, IsCA: true, IPAddresses: []net.IP{net.ParseIP("127.0.0.1")},
	}
	der, err := x509.CreateCertificate(rand.Reader, tpl, tpl, &key.PublicKey, key)
	if err != nil {
		t.Fatal(err)
	}
	cert, _ := x509.ParseCertificate(der)
	pool := x509.NewCertPool()
	pool.AddCert(cert)
	old := mailTLSConfig
	mailTLSConfig = func(host string) *tls.Config {
		return &tls.Config{ServerName: host, RootCAs: pool, MinVersion: tls.VersionTLS12}
	}
	t.Cleanup(func() { mailTLSConfig = old })
	return &tls.Config{Certificates: []tls.Certificate{{Certificate: [][]byte{der}, PrivateKey: key}}}
}

func setTimeout(t *testing.T, d time.Duration) {
	old := mailTimeout
	mailTimeout = d
	t.Cleanup(func() { mailTimeout = old })
}

// mailTestHome stores a fake mailbox credential and registers example.com servers.
func mailTestHome(t *testing.T, imapPort, smtpPort int, caldavURL string) string {
	t.Helper()
	home := t.TempDir()
	if err := secret.SetProviderSecret(home, mailAddressSecret, testMailAddr); err != nil {
		t.Fatal(err)
	}
	if err := secret.SetProviderSecret(home, mailPasswordSecret, testMailPass); err != nil {
		t.Fatal(err)
	}
	oldM, oldC := mailPresets, caldavPresets
	mailPresets = map[string]mailPreset{"example.com": {imapHost: "127.0.0.1", imapPort: imapPort, smtpHost: "127.0.0.1", smtpPort: smtpPort}}
	caldavPresets = map[string]string{}
	if caldavURL != "" {
		caldavPresets["example.com"] = caldavURL
	}
	t.Cleanup(func() { mailPresets, caldavPresets = oldM, oldC })
	return home
}

// ---- fake IMAP --------------------------------------------------------------

type fakeIMAP struct {
	ln       net.Listener
	mu       sync.Mutex
	cmds     []string
	greeting string
	hang     bool
	handle   func(cmd string) string // reply for each command, tagged line included via %TAG%
}

func startFakeIMAP(t *testing.T, cfg *tls.Config, f *fakeIMAP) *fakeIMAP {
	t.Helper()
	ln, err := tls.Listen("tcp", "127.0.0.1:0", cfg)
	if err != nil {
		t.Fatal(err)
	}
	f.ln = ln
	if f.greeting == "" {
		f.greeting = "* OK IMAP4rev1 ready\r\n"
	}
	done := make(chan struct{})
	t.Cleanup(func() { close(done); ln.Close() })
	go func() {
		for {
			c, err := ln.Accept()
			if err != nil {
				return
			}
			go func() {
				defer c.Close()
				if f.hang {
					<-done
					return
				}
				io.WriteString(c, f.greeting)
				r := bufio.NewReader(c)
				for {
					line, err := r.ReadString('\n')
					if err != nil {
						return
					}
					tag, cmd, _ := strings.Cut(strings.TrimRight(line, "\r\n"), " ")
					f.mu.Lock()
					f.cmds = append(f.cmds, cmd)
					f.mu.Unlock()
					if cmd == "LOGOUT" {
						io.WriteString(c, "* BYE bye\r\n"+tag+" OK LOGOUT completed\r\n")
						return
					}
					io.WriteString(c, strings.ReplaceAll(f.handle(cmd), "%TAG%", tag))
				}
			}()
		}
	}()
	return f
}

func (f *fakeIMAP) port() int { return f.ln.Addr().(*net.TCPAddr).Port }

func (f *fakeIMAP) commands() []string {
	f.mu.Lock()
	defer f.mu.Unlock()
	return append([]string(nil), f.cmds...)
}

func fetchReply(uid, literal string) string {
	return "* " + uid + " FETCH (UID " + uid + " BODY[HEADER.FIELDS (SUBJECT FROM DATE MESSAGE-ID)] {" +
		strconv.Itoa(len(literal)) + "}\r\n" + literal + ")\r\n%TAG% OK Fetch completed\r\n"
}

const fetchSpec = "(BODY.PEEK[HEADER.FIELDS (SUBJECT FROM DATE MESSAGE-ID)])"

func TestIMAPListHappyPath(t *testing.T) {
	cfg := testTLS(t)
	subjB64 := base64.StdEncoding.EncodeToString([]byte("Grüße aus Köln"))
	f := startFakeIMAP(t, cfg, &fakeIMAP{handle: func(cmd string) string {
		switch {
		case strings.HasPrefix(cmd, "LOGIN "):
			return "%TAG% OK LOGIN completed\r\n"
		case cmd == "EXAMINE INBOX":
			return "* 3 EXISTS\r\n%TAG% OK [READ-ONLY] done\r\n"
		case cmd == "UID SEARCH ALL":
			return "* SEARCH 11 12 13\r\n%TAG% OK Search done\r\n"
		case cmd == "UID FETCH 13 "+fetchSpec:
			// Literal with a multipart tail the server should never have sent: only the header block is read.
			return fetchReply("13", "Subject: =?UTF-8?B?"+subjB64+"?=\r\nFrom: =?UTF-8?Q?J=C3=BCrgen?= <j@example.com>\r\n"+
				"Date: Tue, 01 Sep 2026 10:00:00 +0000\r\nMessage-ID: <a@example.com>\r\n\r\n--b\r\nContent-Type: text/plain\r\n\r\nhi\r\n--b--\r\n")
		case cmd == "UID FETCH 12 "+fetchSpec:
			// Folded subject, no Subject text in 11 afterwards.
			return fetchReply("12", "Subject: a very\r\n long subject\r\nFrom: bob@example.com\r\nDate: Mon, 31 Aug 2026 09:00:00 +0000\r\n\r\n")
		}
		return "%TAG% BAD unexpected " + cmd + "\r\n"
	}})
	home := mailTestHome(t, f.port(), 1, "")
	out, err := toolMailListIMAP(context.Background(), home, map[string]any{"limit": float64(2)})
	if err != nil {
		t.Fatal(err)
	}
	if out["ok"] != true || out["count"] != 2 || out["query"] != "in:inbox" {
		t.Fatalf("bad envelope: %v", out)
	}
	msgs := out["messages"].([]any)
	m0, m1 := msgs[0].(map[string]any), msgs[1].(map[string]any)
	if m0["id"] != "13" || m0["subject"] != "Grüße aus Köln" || m0["from"] != "Jürgen <j@example.com>" ||
		m0["date"] != "Tue, 01 Sep 2026 10:00:00 +0000" || m0["snippet"] != "" {
		t.Fatalf("message 0: %v", m0)
	}
	if m1["id"] != "12" || m1["subject"] != "a very long subject" || m1["from"] != "bob@example.com" {
		t.Fatalf("message 1: %v", m1)
	}
	want := []string{`LOGIN "user@example.com" "fake-app-password"`, "EXAMINE INBOX", "UID SEARCH ALL",
		"UID FETCH 13 " + fetchSpec, "UID FETCH 12 " + fetchSpec, "LOGOUT"}
	if got := f.commands(); strings.Join(got, "|") != strings.Join(want, "|") {
		t.Fatalf("commands:\n got %q\nwant %q", got, want)
	}
}

func TestIMAPCriteria(t *testing.T) {
	for q, want := range map[string]string{
		"in:inbox": "ALL", "": "ALL", "unread": "UNSEEN", "from: bob@example.com": "FROM bob@example.com",
		"from: Bob Smith": `FROM "Bob Smith"`, "subject:hello world": `SUBJECT "hello world"`,
		"SINCE 1-Aug-2026": "SINCE 1-Aug-2026", "invoice 2026": `TEXT "invoice 2026"`, `say "hi"`: `TEXT "say \"hi\""`,
	} {
		if got := strings.Join(imapCriteria(q), " "); got != want {
			t.Errorf("%q: got %q want %q", q, got, want)
		}
	}
}

func TestIMAPListQueryRejectsInjection(t *testing.T) {
	cfg := testTLS(t)
	f := startFakeIMAP(t, cfg, &fakeIMAP{handle: func(string) string { return "%TAG% OK\r\n" }})
	home := mailTestHome(t, f.port(), 1, "")
	for _, q := range []string{"from: a\rA9 DELETE INBOX", "subject: Grüße"} {
		if _, err := toolMailListIMAP(context.Background(), home, map[string]any{"query": q}); err == nil {
			t.Errorf("query %q accepted", q)
		}
	}
	if len(f.commands()) != 0 {
		t.Fatalf("server was contacted: %q", f.commands())
	}
}

func TestIMAPListSearchNoYieldsEmpty(t *testing.T) {
	cfg := testTLS(t)
	f := startFakeIMAP(t, cfg, &fakeIMAP{handle: func(cmd string) string {
		if strings.HasPrefix(cmd, "UID SEARCH") {
			return "%TAG% NO search failed\r\n"
		}
		return "%TAG% OK\r\n"
	}})
	home := mailTestHome(t, f.port(), 1, "")
	out, err := toolMailListIMAP(context.Background(), home, nil)
	if err != nil || out["ok"] != true || out["count"] != 0 {
		t.Fatalf("%v %v", out, err)
	}
}

func TestIMAPBadLogin(t *testing.T) {
	cfg := testTLS(t)
	f := startFakeIMAP(t, cfg, &fakeIMAP{handle: func(string) string {
		return "%TAG% NO [AUTHENTICATIONFAILED] Invalid credentials (Failure)\r\n"
	}})
	home := mailTestHome(t, f.port(), 1, "")
	_, err := toolMailListIMAP(context.Background(), home, nil)
	if err == nil || !strings.Contains(err.Error(), "That address/app-password was rejected") {
		t.Fatalf("got %v", err)
	}
	if strings.Contains(err.Error(), testMailPass) {
		t.Fatal("password leaked into error")
	}
}

func TestIMAPExamineRefused(t *testing.T) {
	cfg := testTLS(t)
	f := startFakeIMAP(t, cfg, &fakeIMAP{handle: func(cmd string) string {
		if strings.HasPrefix(cmd, "EXAMINE") {
			return "%TAG% NO no such mailbox\r\n"
		}
		return "%TAG% OK\r\n"
	}})
	home := mailTestHome(t, f.port(), 1, "")
	_, err := toolMailListIMAP(context.Background(), home, nil)
	if err == nil || !strings.Contains(err.Error(), "refused to open INBOX") {
		t.Fatalf("got %v", err)
	}
}

func TestIMAPHangTimesOut(t *testing.T) {
	cfg := testTLS(t)
	setTimeout(t, 300*time.Millisecond)
	f := startFakeIMAP(t, cfg, &fakeIMAP{hang: true})
	home := mailTestHome(t, f.port(), 1, "")
	start := time.Now()
	_, err := toolMailListIMAP(context.Background(), home, nil)
	if err == nil || !strings.HasPrefix(err.Error(), "Mail error:") || time.Since(start) > 5*time.Second {
		t.Fatalf("err=%v after %v", err, time.Since(start))
	}
}

func TestIMAPMalformedResponses(t *testing.T) {
	cfg := testTLS(t)
	// Greeting that is not OK.
	f := startFakeIMAP(t, cfg, &fakeIMAP{greeting: "garbage\r\n", handle: func(string) string { return "" }})
	home := mailTestHome(t, f.port(), 1, "")
	if _, err := toolMailListIMAP(context.Background(), home, nil); err == nil {
		t.Fatal("bad greeting accepted")
	}
	// Literal promised but the connection closes first; and an oversized literal.
	for _, reply := range []string{"* 1 FETCH (BODY[] {500}\r\nshort", "* 1 FETCH (BODY[] {99999999}\r\n"} {
		reply := reply
		g := startFakeIMAP(t, cfg, &fakeIMAP{handle: func(cmd string) string {
			if strings.HasPrefix(cmd, "UID SEARCH") {
				return "* SEARCH 1\r\n%TAG% OK\r\n"
			}
			if strings.HasPrefix(cmd, "UID FETCH") {
				return reply
			}
			return "%TAG% OK\r\n"
		}})
		home := mailTestHome(t, g.port(), 1, "")
		setTimeout(t, 500*time.Millisecond)
		if _, err := toolMailListIMAP(context.Background(), home, nil); err == nil {
			t.Fatalf("truncated reply %q accepted", reply)
		}
	}
}

func TestIMAPConnectionRefused(t *testing.T) {
	ln, _ := net.Listen("tcp", "127.0.0.1:0")
	port := ln.Addr().(*net.TCPAddr).Port
	ln.Close()
	home := mailTestHome(t, port, 1, "")
	_, err := toolMailListIMAP(context.Background(), home, nil)
	if err == nil || !strings.HasPrefix(err.Error(), "Mail error:") {
		t.Fatalf("got %v", err)
	}
}

func TestIMAPWrongCertRejected(t *testing.T) {
	cfg := testTLS(t)
	f := startFakeIMAP(t, cfg, &fakeIMAP{handle: func(string) string { return "%TAG% OK\r\n" }})
	home := mailTestHome(t, f.port(), 1, "")
	mailTLSConfig = func(host string) *tls.Config { return &tls.Config{ServerName: host, MinVersion: tls.VersionTLS12} } // system roots only
	if _, err := toolMailListIMAP(context.Background(), home, nil); err == nil {
		t.Fatal("untrusted certificate accepted")
	}
}

// ---- fake SMTP --------------------------------------------------------------

type fakeSMTP struct {
	ln         net.Listener
	cfg        *tls.Config
	implicit   bool
	rejectAuth bool
	hang       bool
	mu         sync.Mutex
	from       string
	rcpts      []string
	data       string
	auth       string
	cmds       []string
}

func startFakeSMTP(t *testing.T, cfg *tls.Config, f *fakeSMTP) *fakeSMTP {
	t.Helper()
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	f.ln, f.cfg = ln, cfg
	done := make(chan struct{})
	t.Cleanup(func() { close(done); ln.Close() })
	go func() {
		for {
			c, err := ln.Accept()
			if err != nil {
				return
			}
			go f.serve(c, done)
		}
	}()
	return f
}

func (f *fakeSMTP) serve(c net.Conn, done chan struct{}) {
	defer func() { c.Close() }()
	if f.hang {
		<-done
		return
	}
	secured := f.implicit
	if secured {
		c = tls.Server(c, f.cfg)
	}
	w := func(s string) { io.WriteString(c, s) }
	r := bufio.NewReader(c)
	w("220 localhost ESMTP ready\r\n")
	for {
		line, err := r.ReadString('\n')
		if err != nil {
			return
		}
		line = strings.TrimRight(line, "\r\n")
		f.mu.Lock()
		f.cmds = append(f.cmds, strings.SplitN(line, " ", 2)[0])
		f.mu.Unlock()
		up := strings.ToUpper(line)
		switch {
		case strings.HasPrefix(up, "EHLO"):
			if secured {
				w("250-localhost\r\n250 AUTH PLAIN\r\n")
			} else {
				w("250-localhost\r\n250-STARTTLS\r\n250 AUTH PLAIN\r\n")
			}
		case up == "STARTTLS":
			w("220 go ahead\r\n")
			c = tls.Server(c, f.cfg)
			r = bufio.NewReader(c)
			secured = true
		case strings.HasPrefix(up, "AUTH PLAIN "):
			raw, _ := base64.StdEncoding.DecodeString(line[len("AUTH PLAIN "):])
			f.mu.Lock()
			f.auth = strings.ReplaceAll(string(raw), "\x00", "|")
			f.mu.Unlock()
			if f.rejectAuth {
				w("535 5.7.8 Username and Password not accepted\r\n")
			} else {
				w("235 2.7.0 Authentication successful\r\n")
			}
		case strings.HasPrefix(up, "MAIL FROM:"):
			f.mu.Lock()
			f.from = line[len("MAIL FROM:"):]
			f.mu.Unlock()
			w("250 OK\r\n")
		case strings.HasPrefix(up, "RCPT TO:"):
			f.mu.Lock()
			f.rcpts = append(f.rcpts, line[len("RCPT TO:"):])
			f.mu.Unlock()
			w("250 OK\r\n")
		case up == "DATA":
			w("354 go ahead\r\n")
			var sb strings.Builder
			for {
				l, err := r.ReadString('\n')
				if err != nil {
					return
				}
				if l == ".\r\n" {
					break
				}
				sb.WriteString(l)
			}
			f.mu.Lock()
			f.data = sb.String()
			f.mu.Unlock()
			w("250 OK queued\r\n")
		case up == "QUIT":
			w("221 bye\r\n")
			return
		default:
			w("502 unexpected\r\n")
		}
	}
}

func (f *fakeSMTP) port() int { return f.ln.Addr().(*net.TCPAddr).Port }

func checkSentMIME(t *testing.T, f *fakeSMTP, wantSubject, wantBody string) {
	t.Helper()
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.auth != "|"+testMailAddr+"|"+testMailPass {
		t.Fatalf("auth = %q", f.auth)
	}
	if f.from != "<user@example.com>" || strings.Join(f.rcpts, ",") != "<bob@example.com>,<carol@example.com>" {
		t.Fatalf("envelope from=%q rcpts=%q", f.from, f.rcpts)
	}
	msg, err := mail.ReadMessage(strings.NewReader(f.data))
	if err != nil {
		t.Fatal(err)
	}
	dec := new(mime.WordDecoder)
	h := msg.Header
	if h.Get("Content-Transfer-Encoding") != "base64" || h.Get("MIME-Version") != "1.0" ||
		h.Get("Content-Type") != `text/plain; charset="utf-8"` || h.Get("From") != testMailAddr ||
		h.Get("To") != "Bob <bob@example.com>, carol@example.com" {
		t.Fatalf("headers: %v", h)
	}
	if s, _ := dec.DecodeHeader(h.Get("Subject")); s != wantSubject {
		t.Fatalf("subject %q", s)
	}
	if !strings.HasPrefix(h.Get("Message-Id"), "<") || h.Get("Date") == "" {
		t.Fatalf("Message-ID/Date: %v", h)
	}
	raw, _ := io.ReadAll(msg.Body)
	for _, l := range strings.Split(strings.TrimSpace(string(raw)), "\n") {
		if len(strings.TrimSpace(l)) > 76 {
			t.Fatalf("base64 line too long: %d", len(l))
		}
	}
	body, err := base64.StdEncoding.DecodeString(strings.NewReplacer("\r", "", "\n", "").Replace(string(raw)))
	if err != nil || string(body) != wantBody {
		t.Fatalf("body %q err %v", body, err)
	}
}

func TestSMTPSendSTARTTLS(t *testing.T) {
	cfg := testTLS(t)
	f := startFakeSMTP(t, cfg, &fakeSMTP{})
	home := mailTestHome(t, 1, f.port(), "")
	long := strings.Repeat("Zeile mit Umlauten äöü. ", 12)
	out, err := toolMailSendSMTP(context.Background(), home, map[string]any{
		"to": "Bob <bob@example.com>, carol@example.com", "subject": "Grüße", "body": "  " + long + "  "})
	if err != nil {
		t.Fatal(err)
	}
	if out["ok"] != true || out["to"] != "Bob <bob@example.com>, carol@example.com" || out["subject"] != "Grüße" ||
		out["thread_id"] != "" || out["message"] != "Sent to Bob <bob@example.com>, carol@example.com: Grüße" ||
		!strings.HasPrefix(out["message_id"].(string), "<") {
		t.Fatalf("result %v", out)
	}
	checkSentMIME(t, f, "Grüße", strings.TrimSpace(long))
	if got := strings.Join(f.cmds, " "); got != "EHLO STARTTLS EHLO AUTH MAIL RCPT RCPT DATA QUIT" {
		t.Fatalf("commands: %s", got)
	}
}

func TestSMTPSendImplicitTLS(t *testing.T) {
	cfg := testTLS(t)
	f := startFakeSMTP(t, cfg, &fakeSMTP{implicit: true})
	old := smtpSSLPort
	smtpSSLPort = f.port()
	t.Cleanup(func() { smtpSSLPort = old })
	home := mailTestHome(t, 1, f.port(), "")
	out, err := toolMailSendSMTP(context.Background(), home, map[string]any{
		"to": "Bob <bob@example.com>, carol@example.com", "subject": "Hi", "text": "short"})
	if err != nil || out["ok"] != true {
		t.Fatalf("%v %v", out, err)
	}
	checkSentMIME(t, f, "Hi", "short")
	if strings.Contains(strings.Join(f.cmds, " "), "STARTTLS") {
		t.Fatalf("implicit TLS must not STARTTLS: %v", f.cmds)
	}
}

func TestSMTPSendValidation(t *testing.T) {
	cfg := testTLS(t)
	f := startFakeSMTP(t, cfg, &fakeSMTP{})
	home := mailTestHome(t, 1, f.port(), "")
	for _, c := range []struct {
		in   map[string]any
		want string
	}{
		{map[string]any{"to": "nope", "body": "x"}, "to must be a valid email address"},
		{map[string]any{"to": "bob@example.com"}, "subject or body is required"},
		{map[string]any{"to": "bob@example.com\r\nBcc: x@example.com", "body": "x"}, "to and subject must be single-line"},
		{map[string]any{"to": "bob@example.com", "subject": "hi\nBcc: x@example.com", "body": "x"}, "to and subject must be single-line"},
	} {
		out, err := toolMailSendSMTP(context.Background(), home, c.in)
		if err != nil || out["ok"] != false || out["error"] != c.want {
			t.Errorf("%v: %v %v", c.in, out, err)
		}
	}
	if len(f.cmds) != 0 {
		t.Fatalf("server contacted: %v", f.cmds)
	}
}

func TestSMTPBadLogin(t *testing.T) {
	cfg := testTLS(t)
	f := startFakeSMTP(t, cfg, &fakeSMTP{rejectAuth: true})
	home := mailTestHome(t, 1, f.port(), "")
	_, err := toolMailSendSMTP(context.Background(), home, map[string]any{"to": "bob@example.com", "body": "x"})
	if err == nil || !strings.HasPrefix(err.Error(), "Mail error: (535,") || strings.Contains(err.Error(), testMailPass) {
		t.Fatalf("got %v", err)
	}
	if f.data != "" {
		t.Fatal("message was sent after failed auth")
	}
}

func TestSMTPHangTimesOut(t *testing.T) {
	cfg := testTLS(t)
	setTimeout(t, 300*time.Millisecond)
	f := startFakeSMTP(t, cfg, &fakeSMTP{hang: true})
	home := mailTestHome(t, 1, f.port(), "")
	start := time.Now()
	_, err := toolMailSendSMTP(context.Background(), home, map[string]any{"to": "bob@example.com", "body": "x"})
	if err == nil || time.Since(start) > 5*time.Second {
		t.Fatalf("err=%v after %v", err, time.Since(start))
	}
}

func TestSMTPNoSTARTTLSRefused(t *testing.T) {
	// A plain server that never offers STARTTLS: the password must not be sent in clear.
	ln, _ := net.Listen("tcp", "127.0.0.1:0")
	t.Cleanup(func() { ln.Close() })
	var mu sync.Mutex
	var seen []string
	go func() {
		c, err := ln.Accept()
		if err != nil {
			return
		}
		defer c.Close()
		io.WriteString(c, "220 hi\r\n")
		r := bufio.NewReader(c)
		for {
			l, err := r.ReadString('\n')
			if err != nil {
				return
			}
			mu.Lock()
			seen = append(seen, l)
			mu.Unlock()
			if strings.HasPrefix(strings.ToUpper(l), "EHLO") {
				io.WriteString(c, "250-hi\r\n250 AUTH PLAIN\r\n")
			} else {
				io.WriteString(c, "502 no\r\n")
			}
		}
	}()
	home := mailTestHome(t, 1, ln.Addr().(*net.TCPAddr).Port, "")
	_, err := toolMailSendSMTP(context.Background(), home, map[string]any{"to": "bob@example.com", "body": "x"})
	if err == nil || !strings.Contains(err.Error(), "STARTTLS extension not supported") {
		t.Fatalf("got %v", err)
	}
	mu.Lock()
	defer mu.Unlock()
	for _, l := range seen {
		if strings.Contains(strings.ToUpper(l), "AUTH") {
			t.Fatalf("auth attempted in clear: %q", l)
		}
	}
}

// ---- CalDAV -----------------------------------------------------------------

const multistatusXML = `<?xml version="1.0" encoding="utf-8"?>
<D:multistatus xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">
 <D:response>
  <D:href>/dav/user%40example.com/events/offsite.ics</D:href>
  <D:propstat><D:prop><D:getetag>"1"</D:getetag><C:calendar-data>BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:offsite-1
DTSTART;VALUE=DATE:20260905
DTEND;VALUE=DATE:20260906
SUMMARY:Team\, offsite &amp; fun
LOCATION:Lake\; north shore
END:VEVENT
END:VCALENDAR
</C:calendar-data></D:prop><D:status>HTTP/1.1 200 OK</D:status></D:propstat>
 </D:response>
 <D:response>
  <D:href>/dav/user%40example.com/events/standup.ics</D:href>
  <D:propstat><D:prop><C:calendar-data><![CDATA[BEGIN:VCALENDAR
BEGIN:VEVENT
UID:standup-1
DTSTART;TZID=Europe/Berlin:20260901T100000
DTEND;TZID=Europe/Berlin:20260901T103000
SUMMARY:Standup
DESCRIPTION:Line one\nline two with a long tail that gets folded by the server
  and continues here
BEGIN:VALARM
ACTION:DISPLAY
DESCRIPTION:alarm text
END:VALARM
END:VEVENT
END:VCALENDAR
]]></C:calendar-data></D:prop></D:propstat>
 </D:response>
</D:multistatus>`

type davCapture struct {
	mu                                    sync.Mutex
	method, uri, auth, depth, ctype, body string
	hits                                  int
}

func (d *davCapture) record(r *http.Request) {
	b, _ := io.ReadAll(r.Body)
	d.mu.Lock()
	defer d.mu.Unlock()
	d.method, d.uri, d.auth, d.depth, d.ctype, d.body = r.Method, r.RequestURI, r.Header.Get("Authorization"), r.Header.Get("Depth"), r.Header.Get("Content-Type"), string(b)
	d.hits++
}

func davHome(t *testing.T, h http.Handler) (string, *httptest.Server) {
	ts := httptest.NewServer(h)
	t.Cleanup(ts.Close)
	return mailTestHome(t, 1, 1, ts.URL+"/dav/{email}/events/"), ts
}

func TestCalDAVListEvents(t *testing.T) {
	cap := &davCapture{}
	home, _ := davHome(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		cap.record(r)
		w.Header().Set("Content-Type", "application/xml")
		w.WriteHeader(207)
		io.WriteString(w, multistatusXML)
	}))
	out, err := toolCalendarListCalDAV(context.Background(), home, map[string]any{
		"time_min": "2026-09-01T00:00:00Z", "time_max": "2026-09-08T00:00:00+00:00", "days": float64(7)})
	if err != nil {
		t.Fatal(err)
	}
	if cap.method != "REPORT" || cap.uri != "/dav/user%40example.com/events/" || cap.depth != "1" ||
		cap.auth != "Basic "+base64.StdEncoding.EncodeToString([]byte(testMailAddr+":"+testMailPass)) ||
		cap.ctype != "application/xml; charset=utf-8" {
		t.Fatalf("request: %+v", cap)
	}
	for _, want := range []string{`<c:calendar-query xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">`,
		`<d:prop><d:getetag/><c:calendar-data/></d:prop>`, `<c:comp-filter name="VCALENDAR"><c:comp-filter name="VEVENT">`,
		`<c:time-range start="20260901T000000Z" end="20260908T000000Z"/>`} {
		if !strings.Contains(cap.body, want) {
			t.Errorf("REPORT body lacks %q:\n%s", want, cap.body)
		}
	}
	if out["ok"] != true || out["count"] != 2 || out["days"] != 7 {
		t.Fatalf("envelope %v", out)
	}
	ev := out["events"].([]any)
	standup, offsite := ev[0].(map[string]any), ev[1].(map[string]any)
	if standup["id"] != "/dav/user%40example.com/events/standup.ics" || standup["title"] != "Standup" ||
		standup["start"] != "2026-09-01T10:00:00" || standup["end"] != "2026-09-01T10:30:00" ||
		standup["description"] != "Line one\nline two with a long tail that gets folded by the server and continues here" ||
		standup["location"] != "" {
		t.Fatalf("standup: %v", standup)
	}
	if offsite["title"] != "Team, offsite & fun" || offsite["start"] != "2026-09-05" || offsite["end"] != "2026-09-06" ||
		offsite["location"] != "Lake; north shore" || offsite["id"] != "/dav/user%40example.com/events/offsite.ics" {
		t.Fatalf("offsite: %v", offsite)
	}
}

func TestCalDAVDefaultRangeAndUTCTimes(t *testing.T) {
	cap := &davCapture{}
	home, _ := davHome(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		cap.record(r)
		w.WriteHeader(207)
		io.WriteString(w, `<multistatus xmlns="DAV:"><response><href>/e1.ics</href><propstat><prop><calendar-data xmlns="urn:ietf:params:xml:ns:caldav">BEGIN:VEVENT
UID:u
DTSTART:20260901T200000Z
DTEND:20260901T210000Z
END:VEVENT</calendar-data></prop></propstat></response></multistatus>`)
	}))
	out, err := toolCalendarListCalDAV(context.Background(), home, nil)
	if err != nil || out["days"] != 7 {
		t.Fatalf("%v %v", out, err)
	}
	if !strings.Contains(cap.body, `<c:time-range start="`) {
		t.Fatal(cap.body)
	}
	e := out["events"].([]any)[0].(map[string]any)
	want := time.Date(2026, 9, 1, 20, 0, 0, 0, time.UTC).In(time.Local).Format("2006-01-02T15:04:05-07:00")
	if e["start"] != want || e["title"] != "(no title)" {
		t.Fatalf("event %v want start %s", e, want)
	}
}

func TestCalDAVCreateEvent(t *testing.T) {
	cap := &davCapture{}
	home, ts := davHome(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		cap.record(r)
		w.WriteHeader(201)
	}))
	out, err := toolCalendarCreateCalDAV(context.Background(), home, map[string]any{
		"title": "Lunch, with Bob;\rEND:VEVENT", "start": "2026-09-01T10:00:00+00:00", "end": "2026-09-01T11:00:00Z",
		"description": "Bring notes\r\nand a very long second line that must be folded because it is longer than seventy-three octets."})
	if err != nil {
		t.Fatal(err)
	}
	if cap.method != "PUT" || cap.ctype != "text/calendar; charset=utf-8" ||
		!strings.HasPrefix(cap.uri, "/dav/user%40example.com/events/remedy-") || !strings.HasSuffix(cap.uri, ".ics") {
		t.Fatalf("request: %+v", cap)
	}
	uid := strings.TrimSuffix(strings.TrimPrefix(cap.uri, "/dav/user%40example.com/events/"), ".ics")
	if len(uid) != len("remedy-")+16 {
		t.Fatalf("uid %q", uid)
	}
	if strings.Count(cap.body, "\r\nEND:VEVENT\r\n") != 1 || strings.Contains(strings.ReplaceAll(cap.body, "\r\n", ""), "\r") {
		t.Fatalf("title escaped badly:\n%q", cap.body)
	}
	lines := strings.Split(strings.TrimSuffix(cap.body, "\r\n"), "\r\n")
	for _, l := range lines {
		if len(l) > 73 {
			t.Errorf("line over 73 octets: %q", l)
		}
	}
	unfolded := strings.Join(icsUnfold(cap.body), "\n")
	for _, want := range []string{"BEGIN:VCALENDAR\nVERSION:2.0\nPRODID:-//Remedy//Partner//EN\nCALSCALE:GREGORIAN\nBEGIN:VEVENT\nUID:" + uid + "\nDTSTAMP:",
		"DTSTART:20260901T100000Z\nDTEND:20260901T110000Z\nSUMMARY:Lunch\\, with Bob\\;\\nEND:VEVENT\nDESCRIPTION:Bring notes\\nand a very long",
		"END:VEVENT\nEND:VCALENDAR\n"} {
		if !strings.Contains(unfolded, want) {
			t.Errorf("PUT body lacks %q:\n%s", want, unfolded)
		}
	}
	if out["ok"] != true || out["id"] != ts.URL+"/dav/user%40example.com/events/"+uid+".ics" || out["title"] != "Lunch, with Bob;\rEND:VEVENT" ||
		out["message"] != "Created calendar event: Lunch, with Bob;\rEND:VEVENT" {
		t.Fatalf("result %v", out)
	}
	want := time.Date(2026, 9, 1, 10, 0, 0, 0, time.UTC).In(time.Local).Format("2006-01-02T15:04:05-07:00")
	if out["start"] != want {
		t.Fatalf("start %v want %v", out["start"], want)
	}
}

func TestCalDAVCreateAllDayAndErrors(t *testing.T) {
	cap := &davCapture{}
	status := 201
	home, _ := davHome(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { cap.record(r); w.WriteHeader(status) }))
	out, err := toolCalendarCreateCalDAV(context.Background(), home, map[string]any{"title": "Holiday", "start": "2026-09-07", "end": "2026-09-08"})
	if err != nil || out["start"] != "2026-09-07" || !strings.Contains(cap.body, "DTSTART;VALUE=DATE:20260907\r\nDTEND;VALUE=DATE:20260908\r\n") {
		t.Fatalf("%v %v\n%s", out, err, cap.body)
	}
	if _, err := toolCalendarCreateCalDAV(context.Background(), home, map[string]any{"title": "x", "start": "garbage", "end": "2026-09-08"}); err == nil ||
		!strings.Contains(err.Error(), "Invalid isoformat string") {
		t.Fatalf("got %v", err)
	}
	if o, _ := toolCalendarCreateCalDAV(context.Background(), home, map[string]any{"title": "x"}); o["error"] != "title, start, and end are required" {
		t.Fatalf("%v", o)
	}
	status = 202
	if _, err := toolCalendarCreateCalDAV(context.Background(), home, map[string]any{"title": "x", "start": "2026-09-07", "end": "2026-09-08"}); err == nil ||
		err.Error() != "Calendar refused the new event (HTTP 202)" {
		t.Fatalf("got %v", err)
	}
}

func TestCalDAVHTTPErrors(t *testing.T) {
	code := 403
	home, _ := davHome(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(code)
		io.WriteString(w, "details here")
	}))
	for c, want := range map[int]string{403: "The calendar rejected that app password.", 401: "The calendar rejected that app password.",
		404: "Calendar not found at that address", 500: "CalDAV 500: details here"} {
		code = c
		_, err := toolCalendarListCalDAV(context.Background(), home, nil)
		if err == nil || !strings.HasPrefix(err.Error(), want) {
			t.Errorf("%d: got %v", c, err)
		}
	}
}

func TestCalDAVRedirects(t *testing.T) {
	var evil int
	evilTS := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { evil++ }))
	defer evilTS.Close()
	mode := "same"
	var hits int
	home, _ := davHome(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		hits++
		switch {
		case mode == "loop":
			http.Redirect(w, r, "/dav/loop/", 302)
		case mode == "evil":
			http.Redirect(w, r, evilTS.URL+"/steal", 302)
		case r.URL.Path == "/dav/moved/":
			w.WriteHeader(207)
			io.WriteString(w, `<multistatus xmlns="DAV:"/>`)
		default:
			http.Redirect(w, r, "/dav/moved/", 301)
		}
	}))
	out, err := toolCalendarListCalDAV(context.Background(), home, nil)
	if err != nil || out["count"] != 0 {
		t.Fatalf("same-origin redirect: %v %v", out, err)
	}
	mode = "evil"
	if _, err := toolCalendarListCalDAV(context.Background(), home, nil); err == nil || !strings.Contains(err.Error(), "Refusing to send calendar credentials") {
		t.Fatalf("got %v", err)
	}
	if evil != 0 {
		t.Fatal("credentials request reached another origin")
	}
	mode = "loop"
	if _, err := toolCalendarListCalDAV(context.Background(), home, nil); err == nil || !strings.Contains(err.Error(), "too many redirects") {
		t.Fatalf("got %v", err)
	}
}

func TestCalDAVHangTimesOut(t *testing.T) {
	setTimeout(t, 300*time.Millisecond)
	home, _ := davHome(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		select {
		case <-r.Context().Done():
		case <-time.After(1500 * time.Millisecond):
		}
	}))
	start := time.Now()
	_, err := toolCalendarListCalDAV(context.Background(), home, nil)
	if err == nil || !strings.HasPrefix(err.Error(), "Calendar unreachable:") || time.Since(start) > 4*time.Second {
		t.Fatalf("err=%v after %v", err, time.Since(start))
	}
}

func TestCalDAVMalformedResponses(t *testing.T) {
	body := "<html>not a multistatus"
	home, _ := davHome(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(207)
		io.WriteString(w, body)
	}))
	if _, err := toolCalendarListCalDAV(context.Background(), home, nil); err == nil || !strings.Contains(err.Error(), "unreadable response") {
		t.Fatalf("got %v", err)
	}
	// Well-formed XML whose iCalendar is junk, and a response with no href: no events, no crash.
	body = `<multistatus xmlns="DAV:"><response><href>/a.ics</href><propstat><prop><calendar-data xmlns="urn:ietf:params:xml:ns:caldav">BEGIN:VEVENT
DTSTART:not-a-date
SUMMARY
END:VEVENT</calendar-data></prop></propstat></response><response><propstat><prop><calendar-data>BEGIN:VEVENT
END:VEVENT</calendar-data></prop></propstat></response></multistatus>`
	out, err := toolCalendarListCalDAV(context.Background(), home, nil)
	if err != nil || out["count"] != 1 {
		t.Fatalf("%v %v", out, err)
	}
	if e := out["events"].([]any)[0].(map[string]any); e["start"] != "not-a-date" || e["title"] != "(no title)" {
		t.Fatalf("%v", e)
	}
}

func TestCalDAVInvalidTimeMin(t *testing.T) {
	home, _ := davHome(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { t.Error("request sent") }))
	if _, err := toolCalendarListCalDAV(context.Background(), home, map[string]any{"time_min": "next tuesday"}); err == nil {
		t.Fatal("bad time_min accepted")
	}
}

func TestCalDAVURLQuoting(t *testing.T) {
	if got := caldavURLFor("a.b+c@gmail.com"); got != "https://www.google.com/calendar/dav/a.b%2Bc%40gmail.com/events/" {
		t.Fatal(got)
	}
	if got := caldavURLFor("x@me.com"); got != "https://caldav.icloud.com/" {
		t.Fatal(got)
	}
	if caldavURLFor("x@yahoo.com") != "" {
		t.Fatal("yahoo has no CalDAV preset")
	}
}

// ---- availability and not-configured ----------------------------------------

func TestIMAPSMTPCalDAVNotConfigured(t *testing.T) {
	home := t.TempDir()
	if imapMailAvailable(home) || caldavAvailable(home) {
		t.Fatal("empty home reports a provider")
	}
	ctx := context.Background()
	if o, err := toolMailListIMAP(ctx, home, nil); err != nil || o["ok"] != false || o["count"] != 0 || o["error"] != googleNoMailMsg || len(o["messages"].([]any)) != 0 {
		t.Fatalf("%v %v", o, err)
	}
	if o, err := toolMailSendSMTP(ctx, home, map[string]any{"to": "a@example.com", "body": "x"}); err != nil || o["ok"] != false || o["error"] != googleNoMailMsg {
		t.Fatalf("%v %v", o, err)
	}
	if o, err := toolCalendarListCalDAV(ctx, home, nil); err != nil || o["ok"] != false || o["count"] != 0 || o["error"] != googleNoCalMsg {
		t.Fatalf("%v %v", o, err)
	}
	if o, err := toolCalendarCreateCalDAV(ctx, home, map[string]any{"title": "t", "start": "2026-09-01", "end": "2026-09-02"}); err != nil || o["ok"] != false || o["error"] != googleNoCalMsg {
		t.Fatalf("%v %v", o, err)
	}
}

func TestIMAPAvailability(t *testing.T) {
	home := mailTestHome(t, 993, 587, "")
	if !imapMailAvailable(home) || caldavAvailable(home) {
		t.Fatal("mail yes, calendar no expected without a CalDAV preset")
	}
	caldavPresets["example.com"] = "http://127.0.0.1/{email}/"
	if !caldavAvailable(home) {
		t.Fatal("calendar expected")
	}
	// Known credential, unknown mail domain: not ready, as in the Python.
	other := t.TempDir()
	secret.SetProviderSecret(other, mailAddressSecret, "me@unknown.invalid")
	secret.SetProviderSecret(other, mailPasswordSecret, testMailPass)
	if imapMailAvailable(other) || caldavAvailable(other) {
		t.Fatal("unknown domain reported available")
	}
	// Alias domains resolve to the base preset.
	if p := presetForAddress("x@hotmail.com"); p.imapHost != "" {
		mailPresets = map[string]mailPreset{"outlook.com": {imapHost: "h", smtpHost: "s"}, "hotmail.com": {alias: "outlook.com"}}
		if presetForAddress("x@hotmail.com").imapHost != "h" {
			t.Fatal("alias not resolved")
		}
	}
}

func TestICSRoundTripHelpers(t *testing.T) {
	if got := icsUnescape(icsEscape("a,b;c\\d\ne")); got != "a,b;c\\d\ne" {
		t.Fatalf("%q", got)
	}
	for in, want := range map[string]string{"20260901": "2026-09-01", "20260901T100000": "2026-09-01T10:00:00", "": "", "junk": "junk"} {
		if got := icsFromDT(in); got != want {
			t.Errorf("icsFromDT(%q)=%q want %q", in, got, want)
		}
	}
	f := icsFold(strings.Repeat("ä", 100))
	for _, l := range strings.Split(f, "\r\n") {
		if len(l) > 73 {
			t.Errorf("fold: %d octets", len(l))
		}
	}
	if strings.Join(icsUnfold(f), "") != strings.Repeat("ä", 100) {
		t.Fatal("fold/unfold mismatch")
	}
}
