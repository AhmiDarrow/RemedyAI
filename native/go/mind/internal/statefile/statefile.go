// Package statefile is the shared disk discipline for the mind packages:
// one mutex per state file (keyed by its path, so every instance in the
// process shares it), atomic writes through secret.WriteFileAtomic (temp file,
// fsync, replace), and JSON objects that keep the fields this code does not
// know, so a file written by the Python worker survives a Go load and save.
package statefile

import (
	"bytes"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"sync"

	"github.com/AhmiDarrow/RemedyAI/native/go/secret"
)

var locks sync.Map // normalized path -> *sync.Mutex

func key(path string) string {
	if abs, err := filepath.Abs(path); err == nil {
		path = abs
	}
	path = filepath.Clean(path)
	if runtime.GOOS == "windows" {
		path = strings.ToLower(path)
	}
	return path
}

// Lock takes the process-wide mutex for path and returns its unlock.
func Lock(path string) func() {
	m, _ := locks.LoadOrStore(key(path), &sync.Mutex{})
	mu := m.(*sync.Mutex)
	mu.Lock()
	return mu.Unlock
}

// Read returns the file's bytes, or nil and no error when it does not exist.
func Read(path string) ([]byte, error) {
	b, err := os.ReadFile(path)
	if errors.Is(err, os.ErrNotExist) {
		return nil, nil
	}
	return b, err
}

// Marshal is indented JSON without HTML escaping, like Python's json.dumps(indent=2, ensure_ascii=False).
func Marshal(v any) ([]byte, error) {
	var buf bytes.Buffer
	enc := json.NewEncoder(&buf)
	enc.SetEscapeHTML(false)
	enc.SetIndent("", "  ")
	if err := enc.Encode(v); err != nil {
		return nil, err
	}
	return buf.Bytes(), nil
}

// WriteJSON writes v atomically. The caller holds Lock(path).
func WriteJSON(path string, v any) error {
	data, err := Marshal(v)
	if err != nil {
		return err
	}
	return secret.WriteFileAtomic(path, data, 0o600)
}

// WriteBytes writes data atomically. The caller holds Lock(path).
func WriteBytes(path string, data []byte) error {
	return secret.WriteFileAtomic(path, data, 0o600)
}

// Remove deletes path under its lock. It reports whether a file was removed.
func Remove(path string) (bool, error) {
	unlock := Lock(path)
	defer unlock()
	err := os.Remove(path)
	if errors.Is(err, os.ErrNotExist) {
		return false, nil
	}
	return err == nil, err
}

// Extra holds the JSON fields a struct does not declare.
type Extra map[string]json.RawMessage

// Decode fills v (a pointer to a plain struct without custom marshalers)
// from data and returns the fields v did not take.
func Decode(data []byte, v any) (Extra, error) {
	var all map[string]json.RawMessage
	if err := json.Unmarshal(data, &all); err != nil {
		return nil, err
	}
	if err := json.Unmarshal(data, v); err != nil {
		return nil, err
	}
	known, err := json.Marshal(v)
	if err != nil {
		return nil, err
	}
	var mine map[string]json.RawMessage
	if err := json.Unmarshal(known, &mine); err != nil {
		return nil, err
	}
	for k := range mine {
		delete(all, k)
	}
	if len(all) == 0 {
		return nil, nil
	}
	return all, nil
}

// Encode marshals v (a plain struct value) with extra merged in; v's fields win.
func Encode(v any, extra Extra) ([]byte, error) {
	known, err := json.Marshal(v)
	if err != nil || len(extra) == 0 {
		return known, err
	}
	var mine map[string]json.RawMessage
	if err := json.Unmarshal(known, &mine); err != nil {
		return nil, err
	}
	out := make(map[string]json.RawMessage, len(mine)+len(extra))
	for k, val := range extra {
		out[k] = val
	}
	for k, val := range mine {
		out[k] = val
	}
	var buf bytes.Buffer
	enc := json.NewEncoder(&buf)
	enc.SetEscapeHTML(false)
	if err := enc.Encode(out); err != nil {
		return nil, err
	}
	return bytes.TrimRight(buf.Bytes(), "\n"), nil
}

// AppendRing appends one line and, when the file passes maxLines, keeps the
// last keep lines. Append and trim happen under the same lock.
func AppendRing(path string, line []byte, maxLines, keep int) error {
	unlock := Lock(path)
	defer unlock()
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		return err
	}
	f, err := os.OpenFile(path, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o600)
	if err != nil {
		return err
	}
	_, werr := f.Write(append(append([]byte(nil), line...), '\n'))
	cerr := f.Close()
	if werr != nil {
		return werr
	}
	if cerr != nil {
		return cerr
	}
	data, err := os.ReadFile(path)
	if err != nil {
		return err
	}
	lines := strings.Split(strings.TrimRight(string(data), "\n"), "\n")
	if len(lines) <= maxLines {
		return nil
	}
	return WriteBytes(path, []byte(strings.Join(lines[len(lines)-keep:], "\n")+"\n"))
}

// TrimTail keeps the last maxBytes of path, cut at a line start, under its lock.
func TrimTail(path string, maxBytes int) (bool, error) {
	if maxBytes <= 0 {
		return false, nil
	}
	unlock := Lock(path)
	defer unlock()
	data, err := Read(path)
	if err != nil || len(data) <= maxBytes {
		return false, err
	}
	tail := data[len(data)-maxBytes:]
	if nl := bytes.IndexByte(tail, '\n'); nl >= 0 && nl < len(tail)-1 {
		tail = tail[nl+1:]
	}
	return true, WriteBytes(path, tail)
}
