package httpapi

import (
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"mime"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"sync"
	"time"
	"unicode/utf8"
)

// mail.* / calendar.* against Google, native Go. Specification: the Python in
// runtime/mail_calendar_tools.py, assistant/providers/google_gmail.py,
// google_calendar.py and assistant/google_oauth.py (get_valid_access_token).
// IMAP/SMTP and CalDAV stay on the Python worker.

// Package-level so tests can point them at an httptest.Server.
var (
	googleGmailAPI      = "https://gmail.googleapis.com/gmail/v1"
	googleCalendarAPI   = "https://www.googleapis.com/calendar/v3"
	googleTokenEndpoint = googleTokenURL
)

var googleTokenMu sync.Mutex

const (
	googleNoMailMsg = "No mail account connected. Connect Google or IMAP in Settings → Personal assistant."
	googleNoCalMsg  = "No calendar connected. Connect Google Calendar in Settings → Personal assistant."
)

// googleMailCalendarAvailable mirrors get_google_gmail / get_google_calendar:
// Google wins whenever the stored tokens are "connected" (a live access token
// or a refresh token); otherwise the Python falls through to IMAP / CalDAV.
func googleMailCalendarAvailable(homeDir string, kind string) bool {
	if kind != "mail" && kind != "calendar" {
		return false
	}
	return loadGoogleTokens(homeDir).connected()
}

// googleValidAccessToken is get_valid_access_token (Go had no refresh yet).
func googleValidAccessToken(homeDir string) (string, error) {
	googleTokenMu.Lock()
	defer googleTokenMu.Unlock()
	t := loadGoogleTokens(homeDir)
	if !t.connected() && t.RefreshToken == "" {
		return "", fmt.Errorf("Google not connected — Settings → Personal assistant → Connect Google.")
	}
	if t.AccessToken != "" && (t.ExpiresAt == 0 || float64(time.Now().Unix()) < t.ExpiresAt-90) {
		return t.AccessToken, nil
	}
	if t.RefreshToken == "" {
		return "", fmt.Errorf("No Google refresh token — Connect Google again.")
	}
	app := loadGoogleAppConfig(homeDir)
	form := map[string]string{
		"client_id":     app.ClientID,
		"refresh_token": t.RefreshToken,
		"grant_type":    "refresh_token",
	}
	if app.ClientSecret != "" {
		form["client_secret"] = app.ClientSecret
	}
	data, err := googleHTTPForm(googleTokenEndpoint, form)
	if err != nil {
		return "", err
	}
	access := strings.TrimSpace(fmt.Sprint(nilToEmpty(data["access_token"])))
	if access == "" {
		msg := firstNonEmpty(
			strings.TrimSpace(fmt.Sprint(nilToEmpty(data["error_description"]))),
			strings.TrimSpace(fmt.Sprint(nilToEmpty(data["error"]))),
			"refresh failed",
		)
		return "", fmt.Errorf("%s", msg)
	}
	t.AccessToken = access
	exp := anyFloat(data["expires_in"], 0)
	if exp == 0 {
		exp = 3600
	}
	t.ExpiresAt = float64(time.Now().UnixNano())/1e9 + exp
	if s := strings.TrimSpace(fmt.Sprint(nilToEmpty(data["scope"]))); s != "" {
		t.Scope = s
	}
	if err := saveGoogleTokens(homeDir, t); err != nil {
		return "", err
	}
	return access, nil
}

// googleAPIRequest is GoogleGmailProvider._request / GoogleCalendarProvider._request.
func googleAPIRequest(ctx context.Context, homeDir, base, ua, product, method, path string, query url.Values, body map[string]any) (map[string]any, error) {
	token, err := googleValidAccessToken(homeDir)
	if err != nil {
		return nil, err
	}
	full := base + path
	if len(query) > 0 {
		full += "?" + query.Encode()
	}
	var rdr io.Reader
	if body != nil {
		b, _ := json.Marshal(body)
		rdr = bytes.NewReader(b)
	}
	req, err := http.NewRequestWithContext(ctx, method, full, rdr)
	if err != nil {
		return nil, err
	}
	req.Header.Set("Authorization", "Bearer "+token)
	req.Header.Set("Accept", "application/json")
	req.Header.Set("User-Agent", ua)
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	resp, err := (&http.Client{Timeout: 30 * time.Second}).Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	raw, _ := io.ReadAll(io.LimitReader(resp.Body, 16<<20))
	if resp.StatusCode < 400 {
		out := map[string]any{}
		if len(bytes.TrimSpace(raw)) > 0 {
			if err := json.Unmarshal(raw, &out); err != nil {
				return nil, err
			}
		}
		return out, nil
	}
	msg := string(raw)
	var parsed map[string]any
	if json.Unmarshal(raw, &parsed) == nil {
		if e, ok := parsed["error"].(map[string]any); ok {
			if m := strings.TrimSpace(pyStr(e["message"])); m != "" {
				msg = m
			}
		}
	} else if msg == "" {
		msg = fmt.Sprintf("HTTP Error %d: %s", resp.StatusCode, http.StatusText(resp.StatusCode))
	}
	low := strings.ToLower(msg)
	code := resp.StatusCode
	if code == 403 && (strings.Contains(low, "has not been used") || strings.Contains(low, "is disabled")) {
		if product == "Gmail" {
			return nil, errors.New("Gmail API is disabled for the Google Cloud project behind this " +
				"OAuth client. Enable **Gmail API** in Google Cloud Console " +
				"(APIs & Services → Library → Gmail API → Enable), wait ~1 minute, " +
				"then retry. Detail: " + msg)
		}
		return nil, errors.New("Google Calendar API is disabled for the OAuth client's Cloud " +
			"project. Enable **Google Calendar API** in Google Cloud Console " +
			"(APIs & Services → Library → Google Calendar API → Enable), " +
			"wait ~1 minute, then retry. Detail: " + msg)
	}
	if product == "Gmail" && (code == 401 || code == 403) &&
		(strings.Contains(low, "insufficient") || strings.Contains(low, "scope") || strings.Contains(low, "permission")) {
		return nil, errors.New("Google hasn't granted this permission yet. Reconnect in " +
			"Settings → Personal assistant → Google (Gmail) → Connect, and " +
			"accept the mail-management permission (archive / mark read). " +
			"Detail: " + msg)
	}
	name := product
	if product == "Calendar" {
		name = "Google Calendar"
	}
	return nil, fmt.Errorf("%s API %d: %s", name, code, msg)
}

func gmailRequest(ctx context.Context, homeDir, method, path string, q url.Values, body map[string]any) (map[string]any, error) {
	return googleAPIRequest(ctx, homeDir, googleGmailAPI, "RemedyDesktop-Gmail/1.0", "Gmail", method, path, q, body)
}

func calendarRequest(ctx context.Context, homeDir, method, path string, q url.Values, body map[string]any) (map[string]any, error) {
	return googleAPIRequest(ctx, homeDir, googleCalendarAPI, "RemedyDesktop-Google-Calendar/1.0", "Calendar", method, path, q, body)
}

// pyStr is str(v or "") for JSON-decoded values.
func pyStr(v any) string {
	switch x := v.(type) {
	case nil:
		return ""
	case string:
		return x
	case bool:
		if !x {
			return ""
		}
		return "True"
	case float64:
		if x == 0 {
			return ""
		}
		return strconv.FormatFloat(x, 'f', -1, 64)
	}
	return fmt.Sprint(v)
}

// pyIntOr is int(v or def).
func pyIntOr(v any, def int) (int, error) {
	switch x := v.(type) {
	case nil:
		return def, nil
	case float64:
		if x == 0 {
			return def, nil
		}
		return int(x), nil
	case int:
		if x == 0 {
			return def, nil
		}
		return x, nil
	case string:
		if x == "" {
			return def, nil
		}
		n, err := strconv.Atoi(strings.TrimSpace(x))
		if err != nil {
			return 0, fmt.Errorf("invalid literal for int(): %q", x)
		}
		if n == 0 {
			return def, nil
		}
		return n, nil
	}
	return 0, fmt.Errorf("int() argument must be a string or a number")
}

func clampInt(n, lo, hi int) int { return max(lo, min(n, hi)) }

func gmailHeaders(meta map[string]any) map[string]string {
	out := map[string]string{}
	payload, _ := meta["payload"].(map[string]any)
	hs, _ := payload["headers"].([]any)
	for _, h := range hs {
		if m, ok := h.(map[string]any); ok {
			out[strings.ToLower(pyStr(m["name"]))] = pyStr(m["value"])
		}
	}
	return out
}

func toolMailListGoogle(ctx context.Context, homeDir string, input map[string]any) (map[string]any, error) {
	if !googleMailCalendarAvailable(homeDir, "mail") {
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
	listing, err := gmailRequest(ctx, homeDir, http.MethodGet, "/users/me/messages",
		url.Values{"maxResults": {strconv.Itoa(limit)}, "q": {query}}, nil)
	if err != nil {
		return nil, err
	}
	rows := []any{}
	items, _ := listing["messages"].([]any)
	for _, it := range items {
		row, ok := it.(map[string]any)
		if !ok || pyStr(row["id"]) == "" {
			continue
		}
		mid := pyStr(row["id"])
		meta, err := gmailRequest(ctx, homeDir, http.MethodGet, "/users/me/messages/"+url.PathEscape(mid),
			url.Values{"format": {"metadata"}, "metadataHeaders": {"From,Subject,Date"}}, nil)
		if err != nil {
			rows = append(rows, map[string]any{"id": mid, "from": "", "subject": "(unavailable)", "snippet": "", "date": ""})
			continue
		}
		h := gmailHeaders(meta)
		rows = append(rows, map[string]any{
			"id":      mid,
			"from":    h["from"],
			"subject": firstNonEmpty(h["subject"], "(no subject)"),
			"snippet": pyStr(meta["snippet"]),
			"date":    h["date"],
		})
	}
	return map[string]any{"ok": true, "messages": rows, "count": len(rows), "query": query}, nil
}

// gmailRawMessage is MIMEText(body, "plain", "utf-8") with To / Subject set,
// base64url encoded. Python's utf-8 charset always uses base64 transfer
// encoding, wrapped at 76 columns.
func gmailRawMessage(to, subject, body string) string {
	b64 := base64.StdEncoding.EncodeToString([]byte(body))
	var wrapped strings.Builder
	for len(b64) > 76 {
		wrapped.WriteString(b64[:76] + "\n")
		b64 = b64[76:]
	}
	if b64 != "" {
		wrapped.WriteString(b64 + "\n")
	}
	subj := subject
	for _, r := range subj {
		if r >= 0x80 {
			subj = mime.BEncoding.Encode("utf-8", subject)
			break
		}
	}
	msg := "Content-Type: text/plain; charset=\"utf-8\"\n" +
		"MIME-Version: 1.0\n" +
		"Content-Transfer-Encoding: base64\n" +
		"To: " + to + "\n" +
		"Subject: " + subj + "\n\n" + wrapped.String()
	return base64.URLEncoding.EncodeToString([]byte(msg))
}

func toolMailSendGoogle(ctx context.Context, homeDir string, input map[string]any) (map[string]any, error) {
	if !googleMailCalendarAvailable(homeDir, "mail") {
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
	data, err := gmailRequest(ctx, homeDir, http.MethodPost, "/users/me/messages/send", nil,
		map[string]any{"raw": gmailRawMessage(to, subject, body)})
	if err != nil {
		return nil, err
	}
	return map[string]any{
		"ok":         true,
		"message_id": pyStr(data["id"]),
		"thread_id":  pyStr(data["threadId"]),
		"to":         to,
		"subject":    subject,
		"message":    "Sent to " + to + ": " + firstNonEmpty(subject, "(no subject)"),
	}, nil
}

func calendarTimes(o any, fallback string) string {
	m, _ := o.(map[string]any)
	return firstNonEmpty(pyStr(m["dateTime"]), pyStr(m["date"]), fallback)
}

func toolCalendarListGoogle(ctx context.Context, homeDir string, input map[string]any) (map[string]any, error) {
	if !googleMailCalendarAvailable(homeDir, "calendar") {
		return map[string]any{"ok": false, "events": []any{}, "count": 0, "error": googleNoCalMsg}, nil
	}
	days, err := pyIntOr(input["days"], 7)
	if err != nil {
		return nil, err
	}
	days = clampInt(days, 1, 60)
	now := time.Now().UTC()
	const layout = "2006-01-02T15:04:05.000000Z"
	timeMin := now.Format(layout)
	timeMax := now.AddDate(0, 0, days).Format(layout)
	if s := pyStr(input["time_min"]); s != "" {
		timeMin = s
	}
	if s := pyStr(input["time_max"]); s != "" {
		timeMax = s
	}
	data, err := calendarRequest(ctx, homeDir, http.MethodGet, "/calendars/primary/events", url.Values{
		"timeMin":      {timeMin},
		"timeMax":      {timeMax},
		"singleEvents": {"true"},
		"orderBy":      {"startTime"},
		"maxResults":   {"40"},
	}, nil)
	if err != nil {
		return nil, err
	}
	rows := []any{}
	items, _ := data["items"].([]any)
	for _, it := range items {
		item, ok := it.(map[string]any)
		if !ok {
			continue
		}
		desc := []rune(pyStr(item["description"]))
		if len(desc) > 500 {
			desc = desc[:500]
		}
		rows = append(rows, map[string]any{
			"id":          pyStr(item["id"]),
			"title":       firstNonEmpty(pyStr(item["summary"]), "(no title)"),
			"start":       calendarTimes(item["start"], ""),
			"end":         calendarTimes(item["end"], ""),
			"location":    pyStr(item["location"]),
			"description": string(desc),
		})
	}
	return map[string]any{"ok": true, "events": rows, "count": len(rows), "days": days}, nil
}

func googleHasTZ(s string) bool {
	s = strings.TrimSpace(s)
	if s == "" {
		return false
	}
	if strings.HasSuffix(s, "Z") || strings.HasSuffix(s, "z") {
		return true
	}
	isDigits := func(x string) bool {
		for _, c := range x {
			if c < '0' || c > '9' {
				return false
			}
		}
		return x != ""
	}
	n := len(s)
	if n >= 6 && s[n-3] == ':' && (s[n-6] == '+' || s[n-6] == '-') && isDigits(s[n-5:n-3]) && isDigits(s[n-2:]) {
		return true
	}
	return n >= 5 && (s[n-5] == '+' || s[n-5] == '-') && isDigits(s[n-4:])
}

// googleEventTime is _event_time: all-day dates stay dates, naive datetimes get
// the local UTC offset so Google does not answer "Missing time zone definition".
func googleEventTime(s string) map[string]any {
	s = strings.TrimSpace(s)
	if len(s) == 10 && s[4] == '-' && s[7] == '-' {
		return map[string]any{"date": s}
	}
	if s != "" && !googleHasTZ(s) {
		s += time.Now().Format("-07:00")
	}
	return map[string]any{"dateTime": s}
}

func toolCalendarCreateGoogle(ctx context.Context, homeDir string, input map[string]any) (map[string]any, error) {
	if !googleMailCalendarAvailable(homeDir, "calendar") {
		return map[string]any{"ok": false, "error": googleNoCalMsg}, nil
	}
	title := strings.TrimSpace(pyStr(input["title"]))
	start := strings.TrimSpace(pyStr(input["start"]))
	end := strings.TrimSpace(pyStr(input["end"]))
	description := strings.TrimSpace(pyStr(input["description"]))
	if title == "" || start == "" || end == "" {
		return map[string]any{"ok": false, "error": "title, start, and end are required"}, nil
	}
	item, err := calendarRequest(ctx, homeDir, http.MethodPost, "/calendars/primary/events", nil, map[string]any{
		"summary":     title,
		"description": description,
		"start":       googleEventTime(start),
		"end":         googleEventTime(end),
	})
	if err != nil {
		return nil, err
	}
	return map[string]any{
		"ok":      true,
		"id":      pyStr(item["id"]),
		"title":   firstNonEmpty(pyStr(item["summary"]), title),
		"start":   calendarTimes(item["start"], start),
		"end":     calendarTimes(item["end"], end),
		"message": "Created calendar event: " + title,
	}, nil
}
