package connect

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"
)

var allowedAuditEvents = map[string]struct{}{
	"pair":               {},
	"revoke":             {},
	"pause":              {},
	"approve-from-phone": {},
}

var secretAuditKeys = map[string]struct{}{
	"secret":          {},
	"ps":              {},
	"payload":         {},
	"token":           {},
	"api_key":         {},
	"bearer":          {},
	"local_api_token": {},
	"authorization":   {},
	"password":        {},
	"hp":              {},
	"pair_secret":     {},
}

var secretAuditFragments = []string{
	"bearer ",
	"local_api_token",
	"api_key=",
	"ps=",
	"authorization:",
}

// AuditPath is ~/.remedy/auth/connect/audit.log.
func AuditPath(home string) (string, error) {
	root, err := ConnectRoot(home)
	if err != nil {
		return "", err
	}
	return filepath.Join(root, "audit.log"), nil
}

// AppendAudit writes one owner-visible audit line. Unknown kinds are ignored.
// Secrets and secret-shaped values are dropped.
func AppendAudit(kind, home string, fields map[string]string) error {
	event := strings.ToLower(strings.TrimSpace(kind))
	if _, ok := allowedAuditEvents[event]; !ok {
		return nil
	}
	parts := []string{
		fmt.Sprintf("ts=%d", time.Now().Unix()),
		fmt.Sprintf("event=%s", event),
	}
	for key, value := range fields {
		safe := safeAuditValue(key, value)
		if safe == "" {
			continue
		}
		parts = append(parts, key+"="+safe)
	}
	line := strings.Join(parts, " ")
	low := strings.ToLower(line)
	for _, frag := range secretAuditFragments {
		if strings.Contains(low, frag) {
			return nil
		}
	}
	path, err := AuditPath(home)
	if err != nil {
		return err
	}
	f, err := os.OpenFile(path, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o600)
	if err != nil {
		return err
	}
	defer f.Close()
	_, err = f.WriteString(line + "\n")
	return err
}

func safeAuditValue(key, value string) string {
	k := strings.ToLower(strings.TrimSpace(key))
	if _, ok := secretAuditKeys[k]; ok {
		return ""
	}
	if strings.HasSuffix(k, "_secret") || strings.HasSuffix(k, "_token") {
		return ""
	}
	text := value
	if len(text) > 120 {
		text = text[:120]
	}
	low := strings.ToLower(text)
	for _, frag := range secretAuditFragments {
		if strings.Contains(low, frag) {
			return ""
		}
	}
	text = strings.ReplaceAll(text, "\n", " ")
	text = strings.ReplaceAll(text, "\r", " ")
	return text
}
