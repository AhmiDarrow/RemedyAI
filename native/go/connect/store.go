package connect

import (
	"encoding/base64"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"runtime"
	"sort"
	"strings"
	"sync"
	"time"

	"github.com/AhmiDarrow/RemedyAI/native/go/secret"
)

const (
	// MaxDevices is the active paired-device cap (Python store.MAX_DEVICES).
	MaxDevices = 3
)

var (
	deviceIDRe  = regexp.MustCompile(`^[a-f0-9]{16,64}$`)
	storeMu     sync.Mutex
	revokedLive = map[string]struct{}{}
)

// Device is a paired-phone record under auth/connect/devices/.
type Device struct {
	ID        string  `json:"id"`
	Name      string  `json:"name"`
	PublicHex string  `json:"public_hex"`
	PairedAt  float64 `json:"paired_at"`
	Revoked   bool    `json:"revoked"`
}

// DevicesDir is ~/.remedy/auth/connect/devices.
func DevicesDir(home string) (string, error) {
	root, err := ConnectRoot(home)
	if err != nil {
		return "", err
	}
	dir := filepath.Join(root, "devices")
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return "", err
	}
	_ = os.Chmod(dir, 0o700)
	return dir, nil
}

func devicePath(deviceID, home string) (string, error) {
	id := strings.ToLower(strings.TrimSpace(deviceID))
	if !deviceIDRe.MatchString(id) {
		return "", fmt.Errorf("invalid device id")
	}
	dir, err := DevicesDir(home)
	if err != nil {
		return "", err
	}
	return filepath.Join(dir, id+".json"), nil
}

// SaveDevice persists a sealed device record.
func SaveDevice(rec Device, home string) (Device, error) {
	id := strings.ToLower(strings.TrimSpace(rec.ID))
	if !deviceIDRe.MatchString(id) {
		return Device{}, fmt.Errorf("invalid device id")
	}
	name := strings.TrimSpace(rec.Name)
	if name == "" {
		name = "phone"
	}
	if len(name) > 80 {
		name = name[:80]
	}
	pairedAt := rec.PairedAt
	if pairedAt == 0 {
		pairedAt = float64(time.Now().UnixNano()) / 1e9
	}
	clean := Device{
		ID:        id,
		Name:      name,
		PublicHex: strings.ToLower(strings.TrimSpace(rec.PublicHex)),
		PairedAt:  pairedAt,
		Revoked:   rec.Revoked,
	}
	path, err := devicePath(id, home)
	if err != nil {
		return Device{}, err
	}
	storeMu.Lock()
	defer storeMu.Unlock()
	if err := writeSealedJSON(path, clean); err != nil {
		return Device{}, err
	}
	if clean.Revoked {
		revokedLive[id] = struct{}{}
	} else {
		delete(revokedLive, id)
	}
	return clean, nil
}

// GetDevice loads one device record, or nil when missing/unreadable.
func GetDevice(deviceID, home string) (*Device, error) {
	path, err := devicePath(deviceID, home)
	if err != nil {
		return nil, nil
	}
	storeMu.Lock()
	defer storeMu.Unlock()
	rec, err := readSealedDevice(path)
	if err != nil || rec == nil {
		return nil, nil
	}
	return rec, nil
}

// ListDevices returns sealed device records, optionally including revoked.
func ListDevices(home string, includeRevoked bool) ([]Device, error) {
	dir, err := DevicesDir(home)
	if err != nil {
		return nil, err
	}
	entries, err := os.ReadDir(dir)
	if err != nil {
		return nil, err
	}
	names := make([]string, 0, len(entries))
	for _, e := range entries {
		if e.IsDir() || !strings.HasSuffix(e.Name(), ".json") {
			continue
		}
		names = append(names, e.Name())
	}
	sort.Strings(names)

	storeMu.Lock()
	defer storeMu.Unlock()
	out := make([]Device, 0, len(names))
	for _, name := range names {
		rec, err := readSealedDevice(filepath.Join(dir, name))
		if err != nil || rec == nil {
			continue
		}
		if !includeRevoked && rec.Revoked {
			continue
		}
		out = append(out, *rec)
	}
	return out, nil
}

// ActiveDeviceCount is the number of non-revoked paired devices.
func ActiveDeviceCount(home string) (int, error) {
	list, err := ListDevices(home, false)
	if err != nil {
		return 0, err
	}
	return len(list), nil
}

// FindDeviceByPublic locates a device by its X25519 public key hex.
func FindDeviceByPublic(publicHex, home string) (*Device, error) {
	want := strings.ToLower(strings.TrimSpace(publicHex))
	if want == "" {
		return nil, nil
	}
	list, err := ListDevices(home, true)
	if err != nil {
		return nil, err
	}
	for i := range list {
		if strings.ToLower(strings.TrimSpace(list[i].PublicHex)) == want {
			rec := list[i]
			return &rec, nil
		}
	}
	return nil, nil
}

// RevokeDevice marks a device revoked and tracks it live.
func RevokeDevice(deviceID, home string) (*Device, error) {
	rec, err := GetDevice(deviceID, home)
	if err != nil || rec == nil {
		return nil, err
	}
	rec.Revoked = true
	saved, err := SaveDevice(*rec, home)
	if err != nil {
		return nil, err
	}
	_ = AppendAudit("revoke", home, map[string]string{"device_id": saved.ID})
	return &saved, nil
}

// DevicePublicMeta is owner-visible device metadata (no public keys / secrets).
type DevicePublicMeta struct {
	ID       string  `json:"id"`
	Name     string  `json:"name"`
	PairedAt float64 `json:"paired_at"`
	Revoked  bool    `json:"revoked"`
}

// DevicePublicMetaList returns active paired devices without public_hex.
// Revoked devices are hidden so revoke removes them from Connect settings.
func DevicePublicMetaList(home string) []DevicePublicMeta {
	list, err := ListDevices(home, false)
	if err != nil || len(list) == 0 {
		return []DevicePublicMeta{}
	}
	out := make([]DevicePublicMeta, 0, len(list))
	for _, rec := range list {
		out = append(out, DevicePublicMeta{
			ID:       rec.ID,
			Name:     rec.Name,
			PairedAt: rec.PairedAt,
			Revoked:  rec.Revoked,
		})
	}
	return out
}

// IsRevokedLive reports whether deviceID was revoked in this process.
func IsRevokedLive(deviceID string) bool {
	id := strings.ToLower(strings.TrimSpace(deviceID))
	storeMu.Lock()
	defer storeMu.Unlock()
	_, ok := revokedLive[id]
	return ok
}

const pausedCacheTTL = 500 * time.Millisecond

var (
	pausedCacheMu sync.Mutex
	pausedCache   = map[string]pausedHit{}
)

type pausedHit struct {
	val bool
	at  time.Time
}

// StatePath is ~/.remedy/auth/connect/state.json.
func StatePath(home string) (string, error) {
	root, err := ConnectRoot(home)
	if err != nil {
		return "", err
	}
	return filepath.Join(root, "state.json"), nil
}

// IsPaused reports the Connect pause flag (short-cached; torn reads keep last good).
func IsPaused(home string) bool {
	path, err := StatePath(home)
	if err != nil {
		return false
	}
	key := path
	now := time.Now()
	pausedCacheMu.Lock()
	hit, ok := pausedCache[key]
	if ok && now.Sub(hit.at) < pausedCacheTTL {
		pausedCacheMu.Unlock()
		return hit.val
	}
	pausedCacheMu.Unlock()

	val := false
	raw, readErr := os.ReadFile(path)
	if readErr == nil && len(raw) > 0 {
		var outer map[string]any
		if json.Unmarshal(raw, &outer) == nil {
			val = pausedFromEnvelope(outer)
		}
	} else if readErr != nil && !os.IsNotExist(readErr) && ok {
		val = hit.val
	}

	pausedCacheMu.Lock()
	pausedCache[key] = pausedHit{val: val, at: now}
	pausedCacheMu.Unlock()
	return val
}

func pausedFromEnvelope(outer map[string]any) bool {
	encoding, _ := outer["encoding"].(string)
	encoding = strings.ToLower(strings.TrimSpace(encoding))
	var inner map[string]any
	switch {
	case encoding == "dpapi":
		blob, _ := outer["dpapi"].(string)
		if blob == "" {
			blob, _ = outer["payload"].(string)
		}
		cipher, err := base64.StdEncoding.DecodeString(blob)
		if err != nil {
			return false
		}
		plain, err := secret.Unprotect(cipher)
		if err != nil {
			return false
		}
		if json.Unmarshal(plain, &inner) != nil {
			return false
		}
	default:
		if payload, ok := outer["payload"].(map[string]any); ok {
			inner = payload
		} else if _, has := outer["paused"]; has {
			inner = outer
		} else {
			return false
		}
	}
	paused, _ := inner["paused"].(bool)
	return paused
}

// SetPaused writes the Connect pause flag and audits transitions.
func SetPaused(paused bool, home string) error {
	was := IsPaused(home)
	path, err := StatePath(home)
	if err != nil {
		return err
	}
	payload := map[string]any{"paused": paused}
	plain, err := json.MarshalIndent(payload, "", "  ")
	if err != nil {
		return err
	}
	envelope := map[string]any{
		"v":        2,
		"encoding": "plain",
		"payload":  payload,
	}
	if runtime.GOOS == "windows" {
		if sealed, err := secret.Protect(plain); err == nil {
			envelope = map[string]any{
				"v":        2,
				"encoding": "dpapi",
				"dpapi":    base64.StdEncoding.EncodeToString(sealed),
			}
		}
	}
	raw, err := json.MarshalIndent(envelope, "", "  ")
	if err != nil {
		return err
	}
	raw = append(raw, '\n')
	storeMu.Lock()
	err = writeBytesAtomic(path, raw)
	storeMu.Unlock()
	if err != nil {
		return err
	}

	pausedCacheMu.Lock()
	pausedCache[path] = pausedHit{val: paused, at: time.Now()}
	pausedCacheMu.Unlock()

	if paused && !was {
		_ = AppendAudit("pause", home, map[string]string{"on": "1"})
	} else if !paused && was {
		_ = AppendAudit("pause", home, map[string]string{"on": "0"})
	}
	return nil
}

func writeSealedJSON(path string, payload Device) error {
	plain, err := json.MarshalIndent(payload, "", "  ")
	if err != nil {
		return err
	}
	var asMap map[string]any
	if err := json.Unmarshal(plain, &asMap); err != nil {
		return err
	}
	plain, err = json.MarshalIndent(asMap, "", "  ")
	if err != nil {
		return err
	}

	envelope := map[string]any{
		"v":        2,
		"encoding": "plain",
		"payload":  asMap,
	}
	if runtime.GOOS == "windows" {
		if sealed, err := secret.Protect(plain); err == nil {
			envelope = map[string]any{
				"v":        2,
				"encoding": "dpapi",
				"dpapi":    base64.StdEncoding.EncodeToString(sealed),
			}
		}
	}
	raw, err := json.MarshalIndent(envelope, "", "  ")
	if err != nil {
		return err
	}
	raw = append(raw, '\n')
	return writeBytesAtomic(path, raw)
}

func readSealedDevice(path string) (*Device, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		if os.IsNotExist(err) {
			return nil, nil
		}
		return nil, err
	}
	if len(raw) == 0 {
		return nil, nil
	}
	var outer map[string]any
	if err := json.Unmarshal(raw, &outer); err != nil {
		return nil, nil
	}
	encoding, _ := outer["encoding"].(string)
	encoding = strings.ToLower(strings.TrimSpace(encoding))
	var inner map[string]any
	switch {
	case encoding == "dpapi":
		blob, _ := outer["dpapi"].(string)
		if blob == "" {
			blob, _ = outer["payload"].(string)
		}
		cipher, err := base64.StdEncoding.DecodeString(blob)
		if err != nil {
			return nil, nil
		}
		plain, err := secret.Unprotect(cipher)
		if err != nil {
			return nil, nil
		}
		if err := json.Unmarshal(plain, &inner); err != nil {
			return nil, nil
		}
	default:
		if payload, ok := outer["payload"].(map[string]any); ok {
			inner = payload
		} else if _, hasID := outer["id"]; hasID {
			inner = outer
		} else if _, hasPub := outer["public_hex"]; hasPub {
			inner = outer
		} else {
			return nil, nil
		}
	}
	rec, err := deviceFromMap(inner)
	if err != nil {
		return nil, nil
	}
	return &rec, nil
}

func deviceFromMap(m map[string]any) (Device, error) {
	id, _ := m["id"].(string)
	name, _ := m["name"].(string)
	pub, _ := m["public_hex"].(string)
	var pairedAt float64
	switch v := m["paired_at"].(type) {
	case float64:
		pairedAt = v
	case json.Number:
		f, _ := v.Float64()
		pairedAt = f
	}
	revoked, _ := m["revoked"].(bool)
	if strings.TrimSpace(id) == "" {
		return Device{}, fmt.Errorf("missing id")
	}
	return Device{
		ID:        strings.ToLower(strings.TrimSpace(id)),
		Name:      name,
		PublicHex: strings.ToLower(strings.TrimSpace(pub)),
		PairedAt:  pairedAt,
		Revoked:   revoked,
	}, nil
}
