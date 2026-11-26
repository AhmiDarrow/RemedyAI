package cli

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"strings"
	"time"

	"github.com/AhmiDarrow/RemedyAI/native/go/httpapi"
	"github.com/AhmiDarrow/RemedyAI/native/go/secret"
)

// runtimeBase is the running remedy-runtime's address: $REMEDY_API or :7400.
func runtimeBase() string {
	if v := strings.TrimSpace(os.Getenv("REMEDY_API")); v != "" {
		return strings.TrimRight(v, "/")
	}
	return "http://127.0.0.1:7400"
}

// apiToken reads the local API token, minting one the way the runtime would
// on first run, so a CLI call before the first serve still authenticates.
func apiToken(home string) string {
	if tok := httpapi.ResolveToken(home); tok != "" {
		return tok
	}
	if !httpapi.AuthEnabled() {
		return ""
	}
	return secret.EnsureLocalAPIToken(home, "")
}

// errUnreachable means no runtime answered.
var errUnreachable = errors.New("runtime unreachable")

// runtimeRequest calls the running runtime and decodes a JSON reply. It
// returns the status code and the decoded body (a string when not JSON).
func runtimeRequest(method, path, home string, body any, timeout time.Duration) (int, any, error) {
	return doRequest(runtimeBase(), method, path, apiToken(home), body, timeout)
}

// doRequest is one JSON call to base+path with an optional bearer token.
func doRequest(base, method, path, token string, body any, timeout time.Duration) (int, any, error) {
	var rdr io.Reader
	if body != nil {
		raw, err := json.Marshal(body)
		if err != nil {
			return 0, nil, err
		}
		rdr = bytes.NewReader(raw)
	}
	req, err := http.NewRequest(method, strings.TrimRight(base, "/")+path, rdr)
	if err != nil {
		return 0, nil, err
	}
	req.Header.Set("Accept", "application/json")
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	if token != "" {
		req.Header.Set("Authorization", "Bearer "+token)
	}
	client := &http.Client{Timeout: timeout}
	resp, err := client.Do(req)
	if err != nil {
		return 0, nil, fmt.Errorf("%w: %v", errUnreachable, err)
	}
	defer resp.Body.Close()
	raw, _ := io.ReadAll(io.LimitReader(resp.Body, 32<<20))
	if len(raw) == 0 {
		return resp.StatusCode, nil, nil
	}
	var payload any
	if json.Unmarshal(raw, &payload) != nil {
		return resp.StatusCode, string(raw), nil
	}
	return resp.StatusCode, payload, nil
}

// detailOf picks a readable error out of a reply body.
func detailOf(payload any) any {
	if m, ok := payload.(map[string]any); ok {
		for _, k := range []string{"error", "detail"} {
			if v, ok := m[k]; ok && v != nil && v != "" {
				return v
			}
		}
	}
	return payload
}
