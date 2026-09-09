package connect

import (
	"strings"
)

// PaneKeys is the ordered set of Connect phone pane flags.
var PaneKeys = []string{
	"live_ui",
	"chat",
	"approvals",
	"sessions",
	"rails",
	"computer_preview",
	"settings_write",
}

// AlwaysOn panes cannot be turned off by a stored payload.
var AlwaysOn = map[string]struct{}{
	"approvals": {},
}

// DefaultPanesMap is the shipping pane defaults (do not mutate).
//
// rails is off by default. It maps to /api/terminal, /api/files and
// /api/browser (see deny.go), so leaving it on would mean a phone that
// finished pairing thirty seconds ago can open a shell on the machine. The
// capability is unchanged — the owner turns it on for a device deliberately,
// and dispatch tells the phone which pane refused it.
var DefaultPanesMap = map[string]bool{
	"live_ui":          true,
	"chat":             true,
	"approvals":        true,
	"sessions":         true,
	"rails":            false,
	"computer_preview": false,
	"settings_write":   false,
}

// DefaultPanes returns a fresh copy of the shipping pane defaults.
func DefaultPanes() map[string]bool {
	out := make(map[string]bool, len(DefaultPanesMap))
	for k, v := range DefaultPanesMap {
		out[k] = v
	}
	return out
}

func asBool(value any) bool {
	switch v := value.(type) {
	case bool:
		return v
	case string:
		s := strings.TrimSpace(strings.ToLower(v))
		switch s {
		case "1", "true", "yes", "on", "enable", "enabled":
			return true
		case "0", "false", "no", "off", "disable", "disabled", "":
			return false
		}
		return s != ""
	case float64:
		return v != 0
	case float32:
		return v != 0
	case int:
		return v != 0
	case int64:
		return v != 0
	case int32:
		return v != 0
	case uint:
		return v != 0
	case uint64:
		return v != 0
	case nil:
		return false
	default:
		return true
	}
}

// NormalizePanes merges raw onto defaults and forces always-on flags.
// Accepts nil, map[string]bool, or map[string]any.
func NormalizePanes(raw any) map[string]bool {
	out := DefaultPanes()
	switch v := raw.(type) {
	case map[string]bool:
		for _, key := range PaneKeys {
			if b, ok := v[key]; ok {
				out[key] = b
			}
		}
	case map[string]any:
		for _, key := range PaneKeys {
			if val, ok := v[key]; ok && val != nil {
				out[key] = asBool(val)
			}
		}
	}
	for key := range AlwaysOn {
		out[key] = true
	}
	return out
}

// PanesFromConfig reads connect_panes from a config mapping (missing → defaults).
func PanesFromConfig(config map[string]any) map[string]bool {
	if config == nil {
		return DefaultPanes()
	}
	return NormalizePanes(config["connect_panes"])
}
