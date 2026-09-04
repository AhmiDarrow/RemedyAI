package httpapi

import (
	"encoding/base64"
	"encoding/json"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"time"

	"github.com/AhmiDarrow/RemedyAI/native/go/secret"
)

func authDir(homeDir string) (string, error) {
	home := ResolveHomeDir(homeDir)
	dir := filepath.Join(home, "auth")
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return "", err
	}
	return dir, nil
}

func sealedAuthEncoding(path string) string {
	raw, err := os.ReadFile(path)
	if err != nil {
		return "missing"
	}
	var outer map[string]any
	if json.Unmarshal(raw, &outer) != nil || outer == nil {
		return "plain"
	}
	if v, _ := outer["v"].(float64); int(v) == 2 {
		if b64, _ := outer["dpapi"].(string); strings.TrimSpace(b64) != "" {
			return "dpapi"
		}
	}
	return "plain"
}

func readSealedJSON(path string) (map[string]any, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	var outer map[string]any
	if err := json.Unmarshal(raw, &outer); err != nil {
		return nil, err
	}
	if outer == nil {
		return nil, os.ErrInvalid
	}
	if v, _ := outer["v"].(float64); int(v) == 2 {
		b64, _ := outer["dpapi"].(string)
		b64 = strings.TrimSpace(b64)
		if b64 == "" {
			return nil, os.ErrInvalid
		}
		cipher, err := base64.StdEncoding.DecodeString(b64)
		if err != nil {
			return nil, err
		}
		plain, err := secret.Unprotect(cipher)
		if err != nil {
			return nil, err
		}
		var inner map[string]any
		if err := json.Unmarshal(plain, &inner); err != nil {
			return nil, err
		}
		if inner == nil {
			return nil, os.ErrInvalid
		}
		return inner, nil
	}
	return outer, nil
}

func writeSealedJSON(path string, payload map[string]any) error {
	if payload == nil {
		payload = map[string]any{}
	}
	plain, err := json.MarshalIndent(payload, "", "  ")
	if err != nil {
		return err
	}
	plain = append(plain, '\n')
	if runtime.GOOS == "windows" {
		if sealed, err := secret.Protect(plain); err == nil && len(sealed) > 0 {
			envelope := map[string]any{
				"v":          2,
				"dpapi":      base64.StdEncoding.EncodeToString(sealed),
				"updated_at": float64(time.Now().UnixNano()) / 1e9,
			}
			return writeJSONAtomic(path, envelope)
		}
	}
	dir := filepath.Dir(path)
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return err
	}
	tmp, err := os.CreateTemp(dir, ".remedy-json-*.tmp")
	if err != nil {
		return err
	}
	tmpPath := tmp.Name()
	cleanup := true
	defer func() {
		if cleanup {
			_ = os.Remove(tmpPath)
		}
	}()
	if err := tmp.Chmod(0o600); err != nil && runtime.GOOS != "windows" {
		_ = tmp.Close()
		return err
	}
	if _, err := tmp.Write(plain); err != nil {
		_ = tmp.Close()
		return err
	}
	if err := tmp.Sync(); err != nil {
		_ = tmp.Close()
		return err
	}
	if err := tmp.Close(); err != nil {
		return err
	}
	_ = os.Remove(path)
	if err := os.Rename(tmpPath, path); err != nil {
		return err
	}
	cleanup = false
	_ = os.Chmod(path, 0o600)
	return nil
}

func removeAuthFile(path string) {
	_ = os.Remove(path)
	dir := filepath.Dir(path)
	base := filepath.Base(path)
	entries, err := os.ReadDir(dir)
	if err != nil {
		return
	}
	prefix := "." + base + "."
	for _, e := range entries {
		name := e.Name()
		if strings.HasPrefix(name, prefix) && strings.HasSuffix(name, ".tmp") {
			_ = os.Remove(filepath.Join(dir, name))
		}
	}
	_ = os.Remove(path + ".tmp")
}
