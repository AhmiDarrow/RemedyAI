package httpapi

import (
	"bufio"
	"bytes"
	"context"
	"crypto/rand"
	"crypto/tls"
	"encoding/base64"
	"encoding/hex"
	"encoding/xml"
	"errors"
	"fmt"
	"io"
	"mime"
	"net"
	"net/http"
	"net/mail"
	"net/smtp"
	"net/textproto"
	"net/url"
	"os"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/AhmiDarrow/RemedyAI/native/go/secret"
	"golang.org/x/text/encoding/ianaindex"
	"golang.org/x/text/transform"
)

// mail.* / calendar.* over IMAP + SMTP and CalDAV, native Go. Specification:
// assistant/providers/imap_smtp.py, caldav.py and runtime/mail_calendar_tools.py.
//
// The Python reads no config file keys. The mailbox is two provider secrets
// (mail_address, mail_app_password) and the servers come from the domain preset
// table below; CalDAV reuses the same credential.

const (
	mailAddressSecret  = "mail_address"
	mailPasswordSecret = "mail_app_password"
	imapMaxLiteral     = 16 << 20
	caldavMaxResponse  = 32 << 20
	caldavMaxError     = 64 << 10
	caldavMaxRedirects = 3
)

// Package-level so tests can point them at local fakes.
var (
	mailTimeout   = 30 * time.Second
	smtpSSLPort   = 465 // this SMTP port is implicit TLS, every other one is STARTTLS
	mailTLSConfig = func(host string) *tls.Config {
		return &tls.Config{ServerName: host, MinVersion: tls.VersionTLS12}
	}
)

type mailPreset struct {
	alias                          string
	imapHost, smtpHost, label, url string
	imapPort, smtpPort             int
}

var mailPresets = map[string]mailPreset{
	"gmail.com":      {imapHost: "imap.gmail.com", imapPort: 993, smtpHost: "smtp.gmail.com", smtpPort: 587, url: "https://myaccount.google.com/apppasswords"},
	"googlemail.com": {alias: "gmail.com"},
	"outlook.com":    {imapHost: "outlook.office365.com", imapPort: 993, smtpHost: "smtp-mail.outlook.com", smtpPort: 587, url: "https://account.microsoft.com/security"},
	"hotmail.com":    {alias: "outlook.com"},
	"live.com":       {alias: "outlook.com"},
	"yahoo.com":      {imapHost: "imap.mail.yahoo.com", imapPort: 993, smtpHost: "smtp.mail.yahoo.com", smtpPort: 587, url: "https://login.yahoo.com/account/security"},
	"fastmail.com":   {imapHost: "imap.fastmail.com", imapPort: 993, smtpHost: "smtp.fastmail.com", smtpPort: 465, url: "https://app.fastmail.com/settings/security/apppasswords"},
	"icloud.com":     {imapHost: "imap.mail.me.com", imapPort: 993, smtpHost: "smtp.mail.me.com", smtpPort: 587, url: "https://appleid.apple.com/account/manage"},
	"me.com":         {alias: "icloud.com"},
}

// caldavPresets: URL templates, {email} is the percent-quoted address.
var caldavPresets = map[string]string{
	"gmail.com":      "https://www.google.com/calendar/dav/{email}/events/",
	"googlemail.com": "https://www.google.com/calendar/dav/{email}/events/",
	"icloud.com":     "https://caldav.icloud.com/",
	"me.com":         "https://caldav.icloud.com/",
	"fastmail.com":   "https://caldav.fastmail.com/dav/calendars/user/{email}/Default/",
}

func mailDomain(address string) string {
	i := strings.LastIndex(address, "@")
	return strings.ToLower(strings.TrimSpace(address[i+1:]))
}

func presetForAddress(address string) mailPreset {
	p := mailPresets[mailDomain(address)]
	if p.alias != "" {
		p = mailPresets[p.alias]
	}
	return p
}

type mailAccount struct {
	address, password, imapHost, smtpHost string
	imapPort, smtpPort                    int
}

// loadMailAccount is load_mail_account: both secrets present and a known server pair.
func loadMailAccount(homeDir string) (mailAccount, bool) {
	addr := strings.TrimSpace(secret.GetProviderSecret(homeDir, mailAddressSecret))
	pwd := strings.TrimSpace(secret.GetProviderSecret(homeDir, mailPasswordSecret))
	if addr == "" || pwd == "" {
		return mailAccount{}, false
	}
	p := presetForAddress(addr)
	a := mailAccount{address: addr, password: pwd, imapHost: p.imapHost, smtpHost: p.smtpHost, imapPort: p.imapPort, smtpPort: p.smtpPort}
	if a.imapPort == 0 {
		a.imapPort = 993
	}
	if a.smtpPort == 0 {
		a.smtpPort = 587
	}
	return a, a.imapHost != "" && a.smtpHost != ""
}

// imapMailAvailable is get_imap_mail() is not None.
func imapMailAvailable(homeDir string) bool {
	_, ok := loadMailAccount(homeDir)
	return ok
}

func caldavURLFor(address string) string {
	tpl := caldavPresets[mailDomain(address)]
	if tpl == "" {
		return ""
	}
	// Python urllib.parse.quote: everything but letters, digits and _.-~/ is escaped.
	q := strings.ReplaceAll(url.QueryEscape(address), "+", "%20")
	return strings.ReplaceAll(tpl, "{email}", q)
}

// caldavAvailable is get_caldav_calendar() is not None.
func caldavAvailable(homeDir string) bool {
	a, ok := loadMailAccount(homeDir)
	return ok && caldavURLFor(a.address) != ""
}

// mailFriendlyError is _friendly_error.
func mailFriendlyError(msg, address string) error {
	low := strings.ToLower(msg)
	u := presetForAddress(address).url
	if strings.Contains(strings.ReplaceAll(low, " ", ""), "authenticationfailed") || strings.Contains(low, "invalid credentials") {
		hint := "That address/app-password was rejected. App passwords need " +
			"2-step verification switched on, and the password is the 16-character " +
			"one the provider generates — not your normal account password."
		if u != "" {
			hint += " Generate one at " + u
		}
		return errors.New(hint)
	}
	if strings.Contains(low, "application-specific password required") {
		s := "This account requires an app password (not the normal password)."
		if u != "" {
			s += " Generate one at " + u
		}
		return errors.New(s)
	}
	if strings.Contains(low, "imap access is disabled") || strings.Contains(low, "imap is disabled") {
		return errors.New("IMAP is turned off for this mailbox — enable IMAP in the mail " +
			"provider's settings, then reconnect.")
	}
	return errors.New("Mail error: " + msg)
}

// tmoConn refreshes a deadline before every read and write, so no single
// operation can hang longer than d (smtplib/imaplib socket timeout semantics).
type tmoConn struct {
	net.Conn
	d time.Duration
}

func (c tmoConn) Read(p []byte) (int, error) {
	_ = c.Conn.SetReadDeadline(time.Now().Add(c.d))
	return c.Conn.Read(p)
}

func (c tmoConn) Write(p []byte) (int, error) {
	_ = c.Conn.SetWriteDeadline(time.Now().Add(c.d))
	return c.Conn.Write(p)
}

func mailDial(ctx context.Context, host string, port int, implicitTLS bool) (net.Conn, error) {
	d := &net.Dialer{Timeout: mailTimeout}
	addr := net.JoinHostPort(host, strconv.Itoa(port))
	c, err := d.DialContext(ctx, "tcp", addr)
	if err != nil {
		return nil, err
	}
	tc := tmoConn{c, mailTimeout}
	if !implicitTLS {
		return tc, nil
	}
	// TLS on top of the timeout wrapper, so the result is a *tls.Conn (net/smtp
	// then knows the link is encrypted) and every handshake read is bounded.
	t := tls.Client(tc, mailTLSConfig(host))
	if err := t.HandshakeContext(ctx); err != nil {
		_ = c.Close()
		return nil, err
	}
	return t, nil
}

// ---- IMAP client ----------------------------------------------------------

type imapResp struct {
	text string   // line(s) with each literal replaced by \x00
	lits [][]byte // literals, in order
}

type imapClient struct {
	c    net.Conn
	r    *bufio.Reader
	seq  int
	stop func() bool
}

var imapLiteralRe = regexp.MustCompile(`\{(\d+)\+?\}$`)

func (m *imapClient) readLine() (string, error) {
	b, err := m.r.ReadSlice('\n')
	if err != nil {
		if errors.Is(err, bufio.ErrBufferFull) {
			return "", errors.New("IMAP response line too long")
		}
		if errors.Is(err, io.EOF) {
			return "", errors.New("connection closed by server")
		}
		return "", err
	}
	return strings.TrimRight(string(b), "\r\n"), nil
}

// readResponse reads one logical response, consuming any {n} literals.
func (m *imapClient) readResponse() (imapResp, error) {
	var out imapResp
	for {
		line, err := m.readLine()
		if err != nil {
			return out, err
		}
		loc := imapLiteralRe.FindStringSubmatchIndex(line)
		if loc == nil {
			out.text += line
			return out, nil
		}
		n, _ := strconv.Atoi(line[loc[2]:loc[3]])
		if n > imapMaxLiteral {
			return out, fmt.Errorf("IMAP literal too large (%d bytes)", n)
		}
		lit := make([]byte, n)
		if _, err := io.ReadFull(m.r, lit); err != nil {
			return out, err
		}
		out.text += line[:loc[0]] + "\x00"
		out.lits = append(out.lits, lit)
	}
}

// cmd sends one tagged command and returns its status (OK/NO/BAD), text and untagged responses.
func (m *imapClient) cmd(line string) (status, text string, untagged []imapResp, err error) {
	m.seq++
	tag := fmt.Sprintf("A%03d", m.seq)
	if _, err = io.WriteString(m.c, tag+" "+line+"\r\n"); err != nil {
		return
	}
	for i := 0; i < 100000; i++ {
		var r imapResp
		if r, err = m.readResponse(); err != nil {
			return
		}
		switch {
		case strings.HasPrefix(r.text, tag+" "):
			rest := strings.TrimSpace(r.text[len(tag)+1:])
			status, text, _ = strings.Cut(rest, " ")
			return strings.ToUpper(status), text, untagged, nil
		case strings.HasPrefix(r.text, "+"):
			return "", "", nil, errors.New("unexpected IMAP continuation request")
		default:
			untagged = append(untagged, r)
		}
	}
	return "", "", nil, errors.New("too many IMAP responses")
}

func imapQuoteArg(s string) (string, error) {
	if strings.ContainsAny(s, "\r\n\x00") {
		return "", errors.New("credentials must not contain control characters")
	}
	return `"` + strings.NewReplacer(`\`, `\\`, `"`, `\"`).Replace(s) + `"`, nil
}

// imapConnect is _imap: implicit-TLS connect (IMAP4_SSL) and LOGIN.
func imapConnect(ctx context.Context, a mailAccount) (*imapClient, error) {
	conn, err := mailDial(ctx, a.imapHost, a.imapPort, true)
	if err != nil {
		return nil, mailFriendlyError(err.Error(), a.address)
	}
	// Closing on ctx cancel unblocks any pending read.
	stop := context.AfterFunc(ctx, func() { _ = conn.Close() })
	m := &imapClient{c: conn, r: bufio.NewReaderSize(conn, 64<<10)}
	fail := func(msg string) (*imapClient, error) {
		stop()
		_ = conn.Close()
		return nil, mailFriendlyError(msg, a.address)
	}
	g, err := m.readResponse()
	if err != nil {
		return fail(err.Error())
	}
	if !strings.HasPrefix(strings.ToUpper(g.text), "* OK") {
		return fail("unexpected IMAP greeting: " + g.text)
	}
	u, err1 := imapQuoteArg(a.address)
	p, err2 := imapQuoteArg(a.password)
	if err1 != nil || err2 != nil {
		return fail("credentials must not contain control characters")
	}
	st, text, _, err := m.cmd("LOGIN " + u + " " + p)
	if err != nil {
		return fail(err.Error())
	}
	if st != "OK" {
		return fail(text)
	}
	m.stop = stop
	return m, nil
}

// logout is a quiet LOGOUT: a failed teardown must not mask a good result.
func (m *imapClient) logout() {
	_, _, _, _ = m.cmd("LOGOUT")
	m.stop()
	_ = m.c.Close()
}

// ---- query mapping (_imap_quote / _imap_criteria) --------------------------

var (
	imapAtomRe     = regexp.MustCompile(`^[A-Za-z0-9_.@-]+$`)
	imapFromRe     = regexp.MustCompile(`(?i)^from:\s*(.+)$`)
	imapSubjectRe  = regexp.MustCompile(`(?i)^subject:\s*(.+)$`)
	imapRawFirstOK = map[string]bool{"ALL": true, "UNSEEN": true, "SEEN": true, "FROM": true, "TO": true,
		"SUBJECT": true, "BODY": true, "SINCE": true, "BEFORE": true}
)

func imapQuote(v string) string {
	if imapAtomRe.MatchString(v) {
		return v
	}
	return `"` + strings.NewReplacer(`\`, `\\`, `"`, `\"`).Replace(v) + `"`
}

func imapCriteria(query string) []string {
	q := strings.TrimSpace(query)
	lq := strings.ToLower(q)
	switch lq {
	case "", "in:inbox", "inbox", "all":
		return []string{"ALL"}
	case "unread", "is:unread", "unseen":
		return []string{"UNSEEN"}
	}
	if m := imapFromRe.FindStringSubmatch(q); m != nil {
		return []string{"FROM", imapQuote(strings.TrimSpace(m[1]))}
	}
	if m := imapSubjectRe.FindStringSubmatch(q); m != nil {
		return []string{"SUBJECT", imapQuote(strings.TrimSpace(m[1]))}
	}
	if f := strings.Fields(q); len(f) > 0 && imapRawFirstOK[strings.ToUpper(f[0])] {
		return f
	}
	return []string{"TEXT", imapQuote(q)}
}

// ---- header decoding -------------------------------------------------------

var mailWordDecoder = &mime.WordDecoder{CharsetReader: func(cs string, in io.Reader) (io.Reader, error) {
	enc, err := ianaindex.MIME.Encoding(cs)
	if err != nil || enc == nil {
		return nil, fmt.Errorf("unsupported charset %q", cs)
	}
	return transform.NewReader(in, enc.NewDecoder()), nil
}}

// mailDecodeHeader is _decode: MIME-decode, and never fail (the raw value on error).
func mailDecodeHeader(v string) string {
	out, err := mailWordDecoder.DecodeHeader(v)
	if err != nil {
		return v
	}
	return out
}

// ---- mail.list -------------------------------------------------------------

func toolMailListIMAP(ctx context.Context, homeDir string, input map[string]any) (map[string]any, error) {
	acct, ok := loadMailAccount(homeDir)
	if !ok {
		return map[string]any{"ok": false, "messages": []any{}, "count": 0, "error": googleNoMailMsg}, nil
	}
	query := strings.TrimSpace(pyStr(input["query"]))
	if query == "" {
		query = "in:inbox"
	}
	limit, err := pyIntOr(input["limit"], 12)
	if err != nil {
		return nil, err
	}
	limit = clampInt(limit, 1, 50)

	criteria := strings.Join(imapCriteria(query), " ")
	if strings.ContainsAny(criteria, "\r\n\x00") || !isASCII(criteria) {
		return nil, errors.New("Mail error: the search query must be single-line ASCII")
	}
	m, err := imapConnect(ctx, acct)
	if err != nil {
		return nil, err
	}
	defer m.logout()
	// EXAMINE is SELECT readonly=True.
	if st, _, _, err := m.cmd("EXAMINE INBOX"); err != nil {
		return nil, errors.New("Mail error: " + err.Error())
	} else if st != "OK" {
		return nil, errors.New("Signed in, but the server refused to open INBOX — " +
			"check the mailbox name and permissions.")
	}
	// UID SEARCH: sequence numbers renumber on expunge, UIDs do not.
	st, _, un, err := m.cmd("UID SEARCH " + criteria)
	if err != nil {
		return nil, errors.New("Mail error: " + err.Error())
	}
	rows := []any{}
	if st == "OK" {
		var ids []string
		for _, r := range un {
			if f := strings.Fields(r.text); len(f) >= 2 && strings.EqualFold(f[0], "*") && strings.EqualFold(f[1], "SEARCH") {
				ids = append(ids, f[2:]...)
			}
		}
		if len(ids) > limit {
			ids = ids[len(ids)-limit:]
		}
		for i := len(ids) - 1; i >= 0; i-- { // newest first
			uid := ids[i]
			if _, err := strconv.ParseUint(uid, 10, 64); err != nil {
				continue
			}
			st, _, un, err := m.cmd("UID FETCH " + uid + " (BODY.PEEK[HEADER.FIELDS (SUBJECT FROM DATE MESSAGE-ID)])")
			if err != nil {
				return nil, errors.New("Mail error: " + err.Error())
			}
			if st != "OK" {
				continue
			}
			var raw []byte
			for _, r := range un {
				if len(r.lits) > 0 {
					raw = r.lits[0]
					break
				}
			}
			if len(raw) == 0 {
				continue
			}
			hdr, _ := textproto.NewReader(bufio.NewReader(bytes.NewReader(raw))).ReadMIMEHeader()
			rows = append(rows, map[string]any{
				"id":      uid,
				"from":    mailDecodeHeader(hdr.Get("From")),
				"subject": firstNonEmpty(mailDecodeHeader(hdr.Get("Subject")), "(no subject)"),
				"snippet": "",
				"date":    hdr.Get("Date"),
			})
		}
	}
	return map[string]any{"ok": true, "messages": rows, "count": len(rows), "query": query}, nil
}

func isASCII(s string) bool {
	for i := 0; i < len(s); i++ {
		if s[i] >= 0x80 {
			return false
		}
	}
	return true
}

// ---- SMTP ------------------------------------------------------------------

// smtpLoginAuth is AUTH LOGIN (smtplib answers username then password without
// looking at the prompt text).
type smtpLoginAuth struct {
	user, pass string
	n          int
}

func (a *smtpLoginAuth) Start(*smtp.ServerInfo) (string, []byte, error) { return "LOGIN", nil, nil }
func (a *smtpLoginAuth) Next(_ []byte, more bool) ([]byte, error) {
	if !more {
		return nil, nil
	}
	a.n++
	if a.n == 1 {
		return []byte(a.user), nil
	}
	return []byte(a.pass), nil
}

func smtpErrText(err error) string {
	var te *textproto.Error
	if errors.As(err, &te) {
		return fmt.Sprintf("(%d, b'%s')", te.Code, te.Msg)
	}
	return err.Error()
}

// smtpConnect is _smtp: port 465 is implicit TLS, anything else STARTTLS; then AUTH.
func smtpConnect(ctx context.Context, a mailAccount) (*smtp.Client, error) {
	conn, err := mailDial(ctx, a.smtpHost, a.smtpPort, a.smtpPort == smtpSSLPort)
	if err != nil {
		return nil, mailFriendlyError(err.Error(), a.address)
	}
	stop := context.AfterFunc(ctx, func() { _ = conn.Close() })
	c, err := smtp.NewClient(conn, a.smtpHost)
	if err != nil {
		stop()
		_ = conn.Close()
		return nil, mailFriendlyError(smtpErrText(err), a.address)
	}
	fail := func(err error) (*smtp.Client, error) {
		stop()
		_ = c.Close()
		return nil, mailFriendlyError(smtpErrText(err), a.address)
	}
	if a.smtpPort != smtpSSLPort {
		if ok, _ := c.Extension("STARTTLS"); !ok {
			return fail(errors.New("STARTTLS extension not supported by server."))
		}
		if err := c.StartTLS(mailTLSConfig(a.smtpHost)); err != nil {
			return fail(err)
		}
	}
	_, params := c.Extension("AUTH")
	mechs := " " + strings.ToUpper(params) + " "
	var auth smtp.Auth
	switch {
	case strings.Contains(mechs, " CRAM-MD5 "):
		auth = smtp.CRAMMD5Auth(a.address, a.password)
	case strings.Contains(mechs, " PLAIN "):
		auth = smtp.PlainAuth("", a.address, a.password, a.smtpHost)
	case strings.Contains(mechs, " LOGIN "):
		auth = &smtpLoginAuth{user: a.address, pass: a.password}
	default:
		return fail(errors.New("SMTP AUTH extension not supported by server."))
	}
	if err := c.Auth(auth); err != nil {
		return fail(err)
	}
	return c, nil
}

// mailEncodeHeader B-encodes a header value that is not plain ASCII (as Python does).
func mailEncodeHeader(v string) string {
	if isASCII(v) {
		return v
	}
	return mime.BEncoding.Encode("utf-8", v)
}

func mailMessageID() string {
	host, _ := os.Hostname()
	if host == "" || strings.ContainsAny(host, "\r\n <>") {
		host = "localhost"
	}
	b := make([]byte, 6)
	_, _ = rand.Read(b)
	return fmt.Sprintf("<%d.%d.%s@%s>", time.Now().Unix(), os.Getpid(), hex.EncodeToString(b), host)
}

// mailBuildMIME is MIMEText(body, "plain", "utf-8") plus From/To/Subject/Message-ID/Date.
func mailBuildMIME(from, to, subject, body, msgID string, now time.Time) []byte {
	b64 := base64.StdEncoding.EncodeToString([]byte(body))
	var w strings.Builder
	for len(b64) > 76 {
		w.WriteString(b64[:76] + "\r\n")
		b64 = b64[76:]
	}
	if b64 != "" {
		w.WriteString(b64 + "\r\n")
	}
	return []byte("Content-Type: text/plain; charset=\"utf-8\"\r\n" +
		"MIME-Version: 1.0\r\n" +
		"Content-Transfer-Encoding: base64\r\n" +
		"From: " + mailEncodeHeader(from) + "\r\n" +
		"To: " + mailEncodeHeader(to) + "\r\n" +
		"Subject: " + mailEncodeHeader(subject) + "\r\n" +
		"Message-ID: " + msgID + "\r\n" +
		"Date: " + now.Format(time.RFC1123Z) + "\r\n\r\n" + w.String())
}

func toolMailSendSMTP(ctx context.Context, homeDir string, input map[string]any) (map[string]any, error) {
	acct, ok := loadMailAccount(homeDir)
	if !ok {
		return map[string]any{"ok": false, "error": googleNoMailMsg}, nil
	}
	to := strings.TrimSpace(pyStr(input["to"]))
	subject := strings.TrimSpace(pyStr(input["subject"]))
	body := strings.TrimSpace(firstNonEmpty(pyStr(input["body"]), pyStr(input["text"])))
	if to == "" || !strings.Contains(to, "@") {
		return map[string]any{"ok": false, "error": "to must be a valid email address"}, nil
	}
	if body == "" && subject == "" {
		return map[string]any{"ok": false, "error": "subject or body is required"}, nil
	}
	// Not in the Python: header injection guard (a newline would add headers).
	if strings.ContainsAny(to+subject, "\r\n") {
		return map[string]any{"ok": false, "error": "to and subject must be single-line"}, nil
	}
	if !utf8.ValidString(body) || !utf8.ValidString(subject) || !utf8.ValidString(to) {
		return map[string]any{"ok": false, "error": "text must be valid UTF-8"}, nil
	}
	list, err := mail.ParseAddressList(to)
	if err != nil || len(list) == 0 {
		return nil, errors.New("At least one valid recipient address is required.")
	}
	rcpts := make([]string, len(list))
	for i, a := range list {
		rcpts[i] = a.Address
	}
	c, err := smtpConnect(ctx, acct)
	if err != nil {
		return nil, err
	}
	stop := context.AfterFunc(ctx, func() { _ = c.Close() })
	defer stop()
	msgID := mailMessageID()
	send := func() error {
		if err := c.Mail(acct.address); err != nil {
			return err
		}
		for _, r := range rcpts {
			if err := c.Rcpt(r); err != nil {
				return err
			}
		}
		w, err := c.Data()
		if err != nil {
			return err
		}
		if _, err := w.Write(mailBuildMIME(acct.address, to, subject, body, msgID, time.Now())); err != nil {
			return err
		}
		return w.Close()
	}
	if err := send(); err != nil {
		_ = c.Close()
		return nil, errors.New(smtpErrText(err))
	}
	_ = c.Quit()
	return map[string]any{
		"ok":         true,
		"message_id": msgID,
		"thread_id":  "",
		"to":         to,
		"subject":    subject,
		"message":    "Sent to " + to + ": " + firstNonEmpty(subject, "(no subject)"),
	}, nil
}

// ---- iCalendar -------------------------------------------------------------

// icsEscape is ics_escape; bare CR is folded into LF first (not in the Python:
// an unescaped CR would let a title add iCalendar lines).
func icsEscape(v string) string {
	v = strings.NewReplacer("\r\n", "\n", "\r", "\n").Replace(v)
	return strings.NewReplacer(`\`, `\\`, ";", `\;`, ",", `\,`, "\n", `\n`).Replace(v)
}

// icsUnescape is ics_unescape (replacements applied in the Python's reverse order).
func icsUnescape(v string) string {
	v = strings.ReplaceAll(v, `\n`, "\n")
	v = strings.ReplaceAll(v, `\,`, ",")
	v = strings.ReplaceAll(v, `\;`, ";")
	return strings.ReplaceAll(v, `\\`, `\`)
}

func icsUnfold(text string) []string {
	var lines []string
	for _, raw := range strings.Split(strings.ReplaceAll(text, "\r\n", "\n"), "\n") {
		if raw != "" && (raw[0] == ' ' || raw[0] == '\t') && len(lines) > 0 {
			lines[len(lines)-1] += raw[1:]
		} else {
			lines = append(lines, raw)
		}
	}
	return lines
}

// icsFold folds at 73 octets on rune boundaries.
func icsFold(line string) string {
	const limit = 73
	if len(line) <= limit {
		return line
	}
	var out []string
	cur := ""
	for _, r := range line {
		max := limit
		if len(out) > 0 {
			max = limit - 1
		}
		if len(cur)+utf8.RuneLen(r) > max {
			out = append(out, cur)
			cur = ""
		}
		cur += string(r)
	}
	out = append(out, cur)
	return strings.Join(out, "\r\n ")
}

// icsParseISO is datetime.fromisoformat for the forms a model or the handler sends.
func icsParseISO(s string) (time.Time, error) {
	iso := strings.ReplaceAll(s, "Z", "+00:00")
	for _, l := range []string{
		"2006-01-02T15:04:05Z07:00", "2006-01-02 15:04:05Z07:00", "2006-01-02T15:04Z07:00",
		"2006-01-02T15:04:05", "2006-01-02 15:04:05", "2006-01-02T15:04", "2006-01-02 15:04",
	} {
		if t, err := time.ParseInLocation(l, iso, time.Local); err == nil {
			return t, nil
		}
	}
	return time.Time{}, fmt.Errorf("Invalid isoformat string: '%s'", s)
}

// icsToDT is to_ics_dt: ("20260901T200000Z", false) timed, ("20260901", true) all-day.
func icsToDT(value string) (string, bool, error) {
	s := strings.TrimSpace(value)
	if s == "" {
		return "", false, errors.New("empty datetime")
	}
	if len(s) == 10 && s[4] == '-' && s[7] == '-' {
		return strings.ReplaceAll(s, "-", ""), true, nil
	}
	t, err := icsParseISO(s)
	if err != nil {
		return "", false, err
	}
	return t.UTC().Format("20060102T150405Z"), false, nil
}

var icsStampRe = regexp.MustCompile(`^(\d{8})T(\d{6})(Z?)$`)

// icsFromDT is from_ics_dt. A TZID parameter is not applied (as in the Python):
// floating and TZID values come back as naive wall time, UTC as local time with offset.
func icsFromDT(v string) string {
	s := strings.TrimSpace(v)
	if s == "" {
		return ""
	}
	if len(s) == 8 && strings.Trim(s, "0123456789") == "" {
		return s[0:4] + "-" + s[4:6] + "-" + s[6:8]
	}
	m := icsStampRe.FindStringSubmatch(s)
	if m == nil {
		return s
	}
	d, t := m[1], m[2]
	base := d[0:4] + "-" + d[4:6] + "-" + d[6:8] + "T" + t[0:2] + ":" + t[2:4] + ":" + t[4:6]
	if m[3] == "" {
		return base
	}
	u, err := time.Parse("2006-01-02T15:04:05", base)
	if err != nil {
		return s
	}
	return u.In(time.Local).Format("2006-01-02T15:04:05-07:00")
}

func icsBuildVEvent(uid, title, start, end, description string) ([]byte, error) {
	dtstart, allDay, err := icsToDT(start)
	if err != nil {
		return nil, err
	}
	dtend := dtstart
	if end != "" {
		if dtend, _, err = icsToDT(end); err != nil {
			return nil, err
		}
	}
	sd := ""
	if allDay {
		sd = ";VALUE=DATE"
	}
	rows := []string{
		"BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Remedy//Partner//EN", "CALSCALE:GREGORIAN",
		"BEGIN:VEVENT", "UID:" + uid,
		"DTSTAMP:" + time.Now().UTC().Format("20060102T150405Z"),
		"DTSTART" + sd + ":" + dtstart,
		"DTEND" + sd + ":" + dtend,
		icsFold("SUMMARY:" + icsEscape(title)),
	}
	if description != "" {
		rows = append(rows, icsFold("DESCRIPTION:"+icsEscape(description)))
	}
	rows = append(rows, "END:VEVENT", "END:VCALENDAR")
	return []byte(strings.Join(rows, "\r\n") + "\r\n"), nil
}

// icsParseVEvents is parse_vevents. Properties of nested components (VALARM)
// are skipped; the Python let an alarm's DESCRIPTION overwrite the event's.
func icsParseVEvents(text string) []map[string]string {
	var out []map[string]string
	var cur map[string]string
	nested := 0
	for _, line := range icsUnfold(text) {
		s := strings.TrimSpace(line)
		switch {
		case s == "BEGIN:VEVENT":
			cur, nested = map[string]string{}, 0
			continue
		case s == "END:VEVENT":
			if cur != nil {
				out = append(out, cur)
			}
			cur = nil
			continue
		case cur == nil:
			continue
		case strings.HasPrefix(s, "BEGIN:"):
			nested++
			continue
		case strings.HasPrefix(s, "END:"):
			if nested > 0 {
				nested--
			}
			continue
		}
		name, value, ok := strings.Cut(s, ":")
		if !ok || nested > 0 {
			continue
		}
		key, _, _ := strings.Cut(name, ";")
		switch key = strings.ToUpper(key); key {
		case "UID", "SUMMARY", "DESCRIPTION", "LOCATION":
			cur[strings.ToLower(key)] = icsUnescape(value)
		case "DTSTART", "DTEND":
			cur[strings.ToLower(key)] = strings.TrimSpace(value)
		}
	}
	return out
}

// ---- CalDAV ----------------------------------------------------------------

func caldavOrigin(u *url.URL) string {
	port := u.Port()
	if port == "" {
		port = map[string]string{"https": "443", "http": "80"}[strings.ToLower(u.Scheme)]
	}
	return strings.ToLower(u.Scheme) + "://" + strings.ToLower(u.Hostname()) + ":" + port
}

// caldavDo is _dav: one request with Basic auth, redirects followed by hand
// (at most 3) and only after the same-origin check the first URL passed, since
// the app password rides on every request.
func caldavDo(ctx context.Context, a mailAccount, base, method, target string, body []byte, depth, ctype string) (int, string, error) {
	baseURL, err := url.Parse(base)
	if err != nil {
		return 0, "", fmt.Errorf("Calendar unreachable: %w", err)
	}
	client := &http.Client{
		Timeout:       mailTimeout,
		CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse },
	}
	auth := "Basic " + base64.StdEncoding.EncodeToString([]byte(a.address+":"+a.password))
	for hop := 0; hop <= caldavMaxRedirects; hop++ {
		tu, err := url.Parse(target)
		if err != nil || caldavOrigin(tu) != caldavOrigin(baseURL) {
			host := target
			if err == nil {
				host = tu.Host
			}
			return 0, "", fmt.Errorf("Refusing to send calendar credentials to '%s' — that is not your calendar host.", host)
		}
		var rdr io.Reader
		if body != nil {
			rdr = bytes.NewReader(body)
		}
		req, err := http.NewRequestWithContext(ctx, method, target, rdr)
		if err != nil {
			return 0, "", fmt.Errorf("Calendar unreachable: %w", err)
		}
		req.Header.Set("Authorization", auth)
		req.Header.Set("Depth", depth)
		req.Header.Set("Content-Type", ctype)
		req.Header.Set("User-Agent", "Remedy-Calendar/1.0")
		resp, err := client.Do(req)
		if err != nil {
			return 0, "", fmt.Errorf("Calendar unreachable: %w", err)
		}
		limit := int64(caldavMaxResponse)
		if resp.StatusCode >= 300 {
			limit = caldavMaxError
		}
		raw, rerr := io.ReadAll(io.LimitReader(resp.Body, limit))
		resp.Body.Close()
		if rerr != nil {
			return 0, "", fmt.Errorf("Calendar unreachable: %w", rerr)
		}
		text := strings.ToValidUTF8(string(raw), "�")
		code := resp.StatusCode
		switch {
		case code >= 200 && code < 300:
			return code, text, nil
		case (code == 301 || code == 302 || code == 303 || code == 307 || code == 308) && resp.Header.Get("Location") != "":
			if hop >= caldavMaxRedirects {
				return 0, "", fmt.Errorf("CalDAV %d: too many redirects (more than %d)", code, caldavMaxRedirects)
			}
			ref, err := url.Parse(resp.Header.Get("Location"))
			if err != nil {
				return 0, "", fmt.Errorf("CalDAV %d: bad redirect", code)
			}
			target = tu.ResolveReference(ref).String()
		case code == 401 || code == 403:
			return 0, "", errors.New("The calendar rejected that app password. Same password as " +
				"mail; make sure 2-step verification is on and the calendar " +
				"is enabled for this account.")
		case code == 404:
			return 0, "", errors.New("Calendar not found at that address — check the account.")
		default:
			return 0, "", fmt.Errorf("CalDAV %d: %s", code, truncRunes(text, 200))
		}
	}
	return 0, "", errors.New("CalDAV: redirect loop")
}

func truncRunes(s string, n int) string {
	if r := []rune(s); len(r) > n {
		return string(r[:n])
	}
	return s
}

type caldavMultistatus struct {
	Responses []struct {
		Href string   `xml:"href"`
		Data []string `xml:"propstat>prop>calendar-data"`
	} `xml:"response"`
}

func caldavEventsFromMultistatus(text string) ([]map[string]any, error) {
	var ms caldavMultistatus
	if err := xml.Unmarshal([]byte(text), &ms); err != nil {
		return nil, fmt.Errorf("Calendar returned an unreadable response: %v", err)
	}
	rows := []map[string]any{}
	for _, r := range ms.Responses {
		href := strings.TrimSpace(r.Href)
		if href == "" {
			continue
		}
		for _, d := range r.Data {
			for _, ev := range icsParseVEvents(d) {
				rows = append(rows, map[string]any{
					"id":          href,
					"title":       firstNonEmpty(ev["summary"], "(no title)"),
					"start":       icsFromDT(ev["dtstart"]),
					"end":         icsFromDT(ev["dtend"]),
					"location":    ev["location"],
					"description": truncRunes(ev["description"], 500),
				})
			}
		}
	}
	sort.SliceStable(rows, func(i, j int) bool { return rows[i]["start"].(string) < rows[j]["start"].(string) })
	return rows, nil
}

func loadCalDAVAccount(homeDir string) (mailAccount, string, bool) {
	a, ok := loadMailAccount(homeDir)
	if !ok {
		return a, "", false
	}
	u := caldavURLFor(a.address)
	return a, u, u != ""
}

func toolCalendarListCalDAV(ctx context.Context, homeDir string, input map[string]any) (map[string]any, error) {
	acct, base, ok := loadCalDAVAccount(homeDir)
	if !ok {
		return map[string]any{"ok": false, "events": []any{}, "count": 0, "error": googleNoCalMsg}, nil
	}
	days, err := pyIntOr(input["days"], 7)
	if err != nil {
		return nil, err
	}
	days = clampInt(days, 1, 60)
	now := time.Now().UTC()
	const layout = "2006-01-02T15:04:05.000000Z"
	timeMin, timeMax := now.Format(layout), now.AddDate(0, 0, days).Format(layout)
	if s := pyStr(input["time_min"]); s != "" {
		timeMin = s
	}
	if s := pyStr(input["time_max"]); s != "" {
		timeMax = s
	}
	start, _, err := icsToDT(timeMin)
	if err != nil {
		return nil, err
	}
	end, _, err := icsToDT(timeMax)
	if err != nil {
		return nil, err
	}
	query := `<?xml version="1.0"?>` +
		`<c:calendar-query xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">` +
		`<d:prop><d:getetag/><c:calendar-data/></d:prop>` +
		`<c:filter><c:comp-filter name="VCALENDAR">` +
		`<c:comp-filter name="VEVENT">` +
		`<c:time-range start="` + start + `" end="` + end + `"/>` +
		`</c:comp-filter></c:comp-filter></c:filter></c:calendar-query>`
	_, text, err := caldavDo(ctx, acct, base, "REPORT", base, []byte(query), "1", "application/xml; charset=utf-8")
	if err != nil {
		return nil, err
	}
	rows, err := caldavEventsFromMultistatus(text)
	if err != nil {
		return nil, err
	}
	out := make([]any, len(rows))
	for i, r := range rows {
		out[i] = r
	}
	return map[string]any{"ok": true, "events": out, "count": len(out), "days": days}, nil
}

func toolCalendarCreateCalDAV(ctx context.Context, homeDir string, input map[string]any) (map[string]any, error) {
	acct, base, ok := loadCalDAVAccount(homeDir)
	if !ok {
		return map[string]any{"ok": false, "error": googleNoCalMsg}, nil
	}
	title := strings.TrimSpace(pyStr(input["title"]))
	start := strings.TrimSpace(pyStr(input["start"]))
	end := strings.TrimSpace(pyStr(input["end"]))
	description := strings.TrimSpace(pyStr(input["description"]))
	if title == "" || start == "" || end == "" {
		return map[string]any{"ok": false, "error": "title, start, and end are required"}, nil
	}
	rnd := make([]byte, 8)
	_, _ = rand.Read(rnd)
	uid := "remedy-" + hex.EncodeToString(rnd)
	body, err := icsBuildVEvent(uid, title, start, end, description)
	if err != nil {
		return nil, err
	}
	target := strings.TrimRight(base, "/") + "/" + uid + ".ics"
	code, _, err := caldavDo(ctx, acct, base, "PUT", target, body, "0", "text/calendar; charset=utf-8")
	if err != nil {
		return nil, err
	}
	if code != 200 && code != 201 && code != 204 {
		return nil, fmt.Errorf("Calendar refused the new event (HTTP %d)", code)
	}
	s, _, _ := icsToDT(start)
	e, _, _ := icsToDT(end)
	return map[string]any{
		"ok":      true,
		"id":      target,
		"title":   title,
		"start":   firstNonEmpty(icsFromDT(s), start),
		"end":     firstNonEmpty(icsFromDT(e), end),
		"message": "Created calendar event: " + title,
	}, nil
}
