package httpapi

import (
	"encoding/json"
	"fmt"
	"strings"

	_ "embed"
)

//go:embed messenger_catalog.json
var messengerCatalogJSON []byte

// messengerFieldSchema is one Settings SPA field on a messenger connector.
type messengerFieldSchema struct {
	Key         string `json:"key"`
	Label       string `json:"label"`
	Kind        string `json:"kind"` // secret | text | bool | list | url
	Placeholder string `json:"placeholder,omitempty"`
	Help        string `json:"help,omitempty"`
	Required    bool   `json:"required,omitempty"`
}

type messengerCatalogEntry struct {
	ID            string                 `json:"id"`
	Name          string                 `json:"name"`
	Description   string                 `json:"description"`
	Status        string                 `json:"status"` // ready | partial | planned
	Inbound       bool                   `json:"inbound"`
	Outbound      bool                   `json:"outbound"`
	DocsURL       string                 `json:"docs_url"`
	Badge         string                 `json:"badge"`
	MaxReplyChars int                    `json:"max_reply_chars"`
	Fields        []messengerFieldSchema `json:"fields"`
}

// messengerCatalog is the production Settings field_schema SSOT (embedded JSON).
var messengerCatalog = mustLoadMessengerCatalog()

func mustLoadMessengerCatalog() []messengerCatalogEntry {
	var out []messengerCatalogEntry
	if err := json.Unmarshal(messengerCatalogJSON, &out); err != nil {
		panic("messenger_catalog.json: " + err.Error())
	}
	if len(out) == 0 {
		panic("messenger_catalog.json: empty catalog")
	}
	for i := range out {
		if strings.TrimSpace(out[i].ID) == "" {
			panic(fmt.Sprintf("messenger_catalog.json: entry %d missing id", i))
		}
		if len(out[i].Fields) == 0 {
			panic(fmt.Sprintf("messenger_catalog.json: %s has no fields", out[i].ID))
		}
	}
	return out
}

func publicFieldsFromSection(entry messengerCatalogEntry, section map[string]any) map[string]any {
	out := map[string]any{}
	if section == nil {
		return out
	}
	for _, f := range entry.Fields {
		if f.Kind == "secret" {
			continue
		}
		val, ok := section[f.Key]
		if !ok {
			continue
		}
		switch f.Kind {
		case "list":
			out[f.Key] = messengerPublicList(val)
		case "bool":
			out[f.Key] = coerceBool(val, false)
		default:
			if val == nil {
				out[f.Key] = ""
			} else {
				out[f.Key] = strings.TrimSpace(fmt.Sprint(val))
			}
		}
	}
	return out
}

// messengerPublicList preserves case (chat IDs / phone numbers are case-sensitive).
func messengerPublicList(val any) []string {
	switch t := val.(type) {
	case []string:
		out := make([]string, 0, len(t))
		for _, x := range t {
			if s := strings.TrimSpace(x); s != "" {
				out = append(out, s)
			}
		}
		return out
	case []any:
		out := make([]string, 0, len(t))
		for _, x := range t {
			if s := strings.TrimSpace(fmt.Sprint(x)); s != "" && s != "<nil>" {
				out = append(out, s)
			}
		}
		return out
	case string:
		parts := strings.FieldsFunc(t, func(r rune) bool { return r == ',' || r == ';' })
		out := make([]string, 0, len(parts))
		for _, p := range parts {
			if s := strings.TrimSpace(p); s != "" {
				out = append(out, s)
			}
		}
		return out
	default:
		return []string{}
	}
}

func fieldSchemaMaps(fields []messengerFieldSchema) []map[string]any {
	out := make([]map[string]any, 0, len(fields))
	for _, f := range fields {
		row := map[string]any{
			"key":      f.Key,
			"label":    f.Label,
			"kind":     f.Kind,
			"required": f.Required,
		}
		if f.Placeholder != "" {
			row["placeholder"] = f.Placeholder
		}
		if f.Help != "" {
			row["help"] = f.Help
		}
		out = append(out, row)
	}
	return out
}
