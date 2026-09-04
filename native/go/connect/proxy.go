package connect

import (
	"bytes"
	"fmt"
	"io"
	"net/http"
	"strconv"
	"strings"
	"time"
)

const (
	// ConnectHopHeader marks loopback hops so management routes can refuse phone minting.
	ConnectHopHeader = "X-Remedy-Connect-Hop"
)

var hopByHop = map[string]struct{}{
	"connection": {}, "keep-alive": {}, "proxy-authenticate": {}, "proxy-authorization": {},
	"te": {}, "trailers": {}, "transfer-encoding": {}, "upgrade": {},
	"authorization": {}, "cookie": {}, "host": {}, "x-remedy-token": {}, "x-api-key": {},
	"x-forwarded-for": {}, "x-forwarded-host": {}, "x-forwarded-proto": {}, "forwarded": {},
	strings.ToLower(ConnectHopHeader): {},
}

var stripResponse = map[string]struct{}{
	"authorization": {}, "www-authenticate": {}, "proxy-authenticate": {},
	"x-remedy-token": {}, "set-cookie": {}, strings.ToLower(ConnectHopHeader): {},
}

// IsSSEContentType reports text/event-stream (and friends).
func IsSSEContentType(contentType string) bool {
	low := strings.ToLower(strings.TrimSpace(strings.Split(contentType, ";")[0]))
	return strings.Contains(low, "text/event-stream")
}

func filterRequestHeaders(headers string) http.Header {
	out := make(http.Header)
	for _, line := range strings.Split(headers, "\n") {
		line = strings.TrimSpace(strings.TrimSuffix(line, "\r"))
		if line == "" {
			continue
		}
		name, value, ok := strings.Cut(line, ":")
		if !ok {
			continue
		}
		lk := strings.ToLower(strings.TrimSpace(name))
		if _, drop := hopByHop[lk]; drop || strings.HasPrefix(lk, "proxy-") {
			continue
		}
		out.Add(strings.TrimSpace(name), strings.TrimSpace(value))
	}
	return out
}

func filterResponseHeaders(h http.Header, sse bool) http.Header {
	out := make(http.Header)
	for key, vals := range h {
		lk := strings.ToLower(key)
		if lk == "transfer-encoding" || lk == "connection" || lk == "keep-alive" {
			continue
		}
		if _, drop := stripResponse[lk]; drop {
			continue
		}
		if sse && lk == "content-length" {
			continue
		}
		for _, v := range vals {
			out.Add(key, v)
		}
	}
	return out
}

// EncodeHTTPHead builds an HTTP/1.1 response head (status + headers + blank line).
func EncodeHTTPHead(status int, reason string, headers http.Header) []byte {
	if reason == "" {
		reason = "OK"
	}
	var b strings.Builder
	b.WriteString(fmt.Sprintf("HTTP/1.1 %d %s\r\n", status, reason))
	for key, vals := range headers {
		for _, v := range vals {
			b.WriteString(key)
			b.WriteString(": ")
			b.WriteString(v)
			b.WriteString("\r\n")
		}
	}
	b.WriteString("\r\n")
	return []byte(b.String())
}

// EncodeHTTPError builds a JSON error response as HTTP/1.1 bytes.
func EncodeHTTPError(status int, reason string, body []byte) []byte {
	h := make(http.Header)
	h.Set("Content-Type", "application/json")
	h.Set("Content-Length", strconv.Itoa(len(body)))
	h.Set("Cache-Control", "no-store")
	h.Set("Connection", "keep-alive")
	return append(EncodeHTTPHead(status, reason, h), body...)
}

func chunkedBlock(chunk []byte) []byte {
	out := make([]byte, 0, len(chunk)+32)
	out = append(out, []byte(fmt.Sprintf("%X\r\n", len(chunk)))...)
	out = append(out, chunk...)
	out = append(out, '\r', '\n')
	return out
}

// ProxyRequest is one loopback hop to the local API.
type ProxyRequest struct {
	Method      string
	Path        string
	Query       string
	Headers     string
	Body        []byte
	SidecarPort int
	APIKey      string
}

// IterProxyResponse yields HTTP/1.1 response bytes as they arrive (SSE chunked).
func IterProxyResponse(req ProxyRequest, emit func([]byte) error) error {
	port := req.SidecarPort
	if port <= 0 {
		port = 7400
	}
	if port > 65535 {
		return fmt.Errorf("%w: sidecar port out of range", ErrSession)
	}
	safe, ok := SanitizeOriginPath(req.Path)
	if !ok {
		body := []byte(`{"error":"path"}`)
		return emit(EncodeHTTPError(400, "Bad Request", body))
	}
	q := strings.TrimPrefix(strings.TrimSpace(req.Query), "?")
	url := fmt.Sprintf("http://127.0.0.1:%d%s", port, safe)
	if q != "" {
		url = url + "?" + q
	}
	method := strings.ToUpper(strings.TrimSpace(req.Method))
	if method == "" {
		method = "GET"
	}
	var bodyReader io.Reader
	if len(req.Body) > 0 {
		bodyReader = bytes.NewReader(req.Body)
	}
	httpReq, err := http.NewRequest(method, url, bodyReader)
	if err != nil {
		return err
	}
	for k, vals := range filterRequestHeaders(req.Headers) {
		for _, v := range vals {
			httpReq.Header.Add(k, v)
		}
	}
	httpReq.Host = fmt.Sprintf("127.0.0.1:%d", port)
	httpReq.Header.Set(ConnectHopHeader, "1")
	if tok := strings.TrimSpace(req.APIKey); tok != "" {
		httpReq.Header.Set("Authorization", "Bearer "+tok)
	}
	client := &http.Client{
		Timeout: 0,
		CheckRedirect: func(*http.Request, []*http.Request) error {
			return http.ErrUseLastResponse
		},
		Transport: &http.Transport{
			ResponseHeaderTimeout: 10 * time.Second,
		},
	}
	resp, err := client.Do(httpReq)
	if err != nil {
		body := []byte(`{"error":"pipe","reason":"pipe"}`)
		return emit(EncodeHTTPError(502, "Bad Gateway", body))
	}
	defer resp.Body.Close()

	ctype := resp.Header.Get("Content-Type")
	sse := IsSSEContentType(ctype)
	outHeaders := filterResponseHeaders(resp.Header, sse)
	reason := resp.Status
	if i := strings.IndexByte(reason, ' '); i >= 0 && i+1 < len(reason) {
		reason = reason[i+1:]
	}
	if reason == "" {
		reason = "OK"
	}
	if sse {
		outHeaders.Set("Cache-Control", "no-cache")
		if outHeaders.Get("Content-Type") == "" {
			outHeaders.Set("Content-Type", "text/event-stream")
		}
		outHeaders.Set("Transfer-Encoding", "chunked")
		outHeaders.Del("Content-Length")
		if err := emit(EncodeHTTPHead(resp.StatusCode, reason, outHeaders)); err != nil {
			return err
		}
		buf := make([]byte, 16384)
		for {
			n, rerr := resp.Body.Read(buf)
			if n > 0 {
				if err := emit(chunkedBlock(buf[:n])); err != nil {
					return err
				}
			}
			if rerr == io.EOF {
				break
			}
			if rerr != nil {
				return rerr
			}
		}
		return emit([]byte("0\r\n\r\n"))
	}

	hasCL := outHeaders.Get("Content-Length") != ""
	if !hasCL {
		outHeaders.Set("Transfer-Encoding", "chunked")
		if err := emit(EncodeHTTPHead(resp.StatusCode, reason, outHeaders)); err != nil {
			return err
		}
		buf := make([]byte, 16384)
		for {
			n, rerr := resp.Body.Read(buf)
			if n > 0 {
				if err := emit(chunkedBlock(buf[:n])); err != nil {
					return err
				}
			}
			if rerr == io.EOF {
				break
			}
			if rerr != nil {
				return rerr
			}
		}
		return emit([]byte("0\r\n\r\n"))
	}

	if err := emit(EncodeHTTPHead(resp.StatusCode, reason, outHeaders)); err != nil {
		return err
	}
	_, err = io.Copy(&emitWriter{emit: emit}, resp.Body)
	return err
}

type emitWriter struct {
	emit func([]byte) error
}

func (w *emitWriter) Write(p []byte) (int, error) {
	if len(p) == 0 {
		return 0, nil
	}
	cp := append([]byte(nil), p...)
	if err := w.emit(cp); err != nil {
		return 0, err
	}
	return len(p), nil
}
