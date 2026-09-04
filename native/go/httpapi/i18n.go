package httpapi

import (
	"encoding/json"
	"net/http"
	"strings"
	"sync"

	_ "embed"
)

//go:embed i18n_data.json
var i18nDataJSON []byte

const i18nAuto = "auto"

type i18nLang struct {
	ID         string `json:"id"`
	NameEN     string `json:"name_en"`
	NameNative string `json:"name_native"`
	RTL        bool   `json:"rtl"`
	Chrome     bool   `json:"chrome"`
}

type i18nBundle struct {
	EN         map[string]string            `json:"en"`
	Overlays   map[string]map[string]string `json:"overlays"`
	Languages  []i18nLang                   `json:"languages"`
	byID       map[string]i18nLang
	aliases    map[string]string
}

var (
	i18nOnce       sync.Once
	i18nBundleData *i18nBundle
	i18nErr        error
)

func loadI18nBundle() (*i18nBundle, error) {
	i18nOnce.Do(func() {
		var raw i18nBundle
		if err := json.Unmarshal(i18nDataJSON, &raw); err != nil {
			i18nErr = err
			return
		}
		if raw.EN == nil {
			raw.EN = map[string]string{}
		}
		if raw.Overlays == nil {
			raw.Overlays = map[string]map[string]string{}
		}
		raw.byID = make(map[string]i18nLang, len(raw.Languages))
		for _, row := range raw.Languages {
			id := strings.ToLower(strings.TrimSpace(row.ID))
			if id == "" || id == i18nAuto {
				continue
			}
			raw.byID[id] = row
		}
		raw.aliases = map[string]string{
			"en-us": "en", "en-gb": "en", "en-au": "en", "en-ca": "en",
			"es-mx": "es", "es-ar": "es", "es-es": "es", "es-419": "es",
			"pt-br": "pt", "pt-pt": "pt",
			"zh": "zh-Hans", "zh-cn": "zh-Hans", "zh-sg": "zh-Hans",
			"zh-tw": "zh-Hant", "zh-hk": "zh-Hant", "zh-mo": "zh-Hant",
			"nb": "no", "nn": "no", "nb-no": "no",
			"fil-ph": "fil", "tl": "fil",
			"iw": "he", "in": "id", "jp": "ja", "ua": "uk",
			"fa-ir": "fa", "ar-sa": "ar", "ar-eg": "ar",
		}
		i18nBundleData = &raw
	})
	return i18nBundleData, i18nErr
}

func normalizeUILanguage(raw string) string {
	s := strings.TrimSpace(raw)
	if s == "" {
		return i18nAuto
	}
	low := strings.ToLower(s)
	if low == i18nAuto || low == "default" || low == "system" || low == "os" {
		return i18nAuto
	}
	key := strings.ReplaceAll(low, "_", "-")
	b, err := loadI18nBundle()
	if err != nil || b == nil {
		return i18nAuto
	}
	if row, ok := b.byID[key]; ok {
		return row.ID
	}
	if alias, ok := b.aliases[key]; ok {
		if row, ok := b.byID[strings.ToLower(alias)]; ok {
			return row.ID
		}
		return alias
	}
	prefix := key
	if i := strings.IndexByte(key, '-'); i > 0 {
		prefix = key[:i]
	}
	if row, ok := b.byID[prefix]; ok {
		return row.ID
	}
	if alias, ok := b.aliases[prefix]; ok {
		if row, ok := b.byID[strings.ToLower(alias)]; ok {
			return row.ID
		}
		return alias
	}
	return i18nAuto
}

func resolveUILanguage(stored, hint string) string {
	code := normalizeUILanguage(stored)
	if code != i18nAuto {
		return code
	}
	hinted := normalizeUILanguage(hint)
	if hinted != i18nAuto {
		return hinted
	}
	return "en"
}

func isRTL(code string) bool {
	b, err := loadI18nBundle()
	if err != nil || b == nil {
		return false
	}
	n := normalizeUILanguage(code)
	if n == i18nAuto {
		return false
	}
	row, ok := b.byID[strings.ToLower(n)]
	return ok && row.RTL
}

func chromeCatalog(stored, hint string) map[string]string {
	b, err := loadI18nBundle()
	if err != nil || b == nil {
		return map[string]string{}
	}
	out := make(map[string]string, len(b.EN)+8)
	for k, v := range b.EN {
		out[k] = v
	}
	code := resolveUILanguage(stored, hint)
	if code == "en" {
		return out
	}
	if overlay := b.Overlays[code]; overlay != nil {
		for k, v := range overlay {
			if strings.TrimSpace(v) != "" {
				out[k] = v
			}
		}
	}
	return out
}

func publicLanguageList() []map[string]any {
	b, err := loadI18nBundle()
	rows := []map[string]any{
		{
			"id":          i18nAuto,
			"name_en":     "Auto",
			"name_native": "Auto",
			"rtl":         false,
			"chrome":      true,
		},
	}
	if err != nil || b == nil {
		return rows
	}
	for _, row := range b.Languages {
		id := strings.TrimSpace(row.ID)
		if id == "" || strings.EqualFold(id, i18nAuto) {
			continue
		}
		rows = append(rows, map[string]any{
			"id":          row.ID,
			"name_en":     row.NameEN,
			"name_native": row.NameNative,
			"rtl":         row.RTL,
			"chrome":      row.Chrome,
		})
	}
	return rows
}

func catalogPayload(stored, hint string) map[string]any {
	resolved := resolveUILanguage(stored, hint)
	storedN := normalizeUILanguage(stored)
	chrome := resolved == "en"
	if !chrome {
		if b, err := loadI18nBundle(); err == nil && b != nil {
			if row, ok := b.byID[strings.ToLower(resolved)]; ok {
				chrome = row.Chrome
			}
		}
	}
	dir := "ltr"
	if isRTL(resolved) {
		dir = "rtl"
	}
	return map[string]any{
		"ui_language": storedN,
		"resolved":    resolved,
		"dir":         dir,
		"chrome":      chrome,
		"catalog":     chromeCatalog(stored, hint),
		"languages":   publicLanguageList(),
	}
}

func (s *Server) handleGetI18n(w http.ResponseWriter, r *http.Request) {
	if _, err := loadI18nBundle(); err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]string{
			"detail": "i18n catalog unavailable",
		})
		return
	}
	q := r.URL.Query()
	lang := strings.TrimSpace(q.Get("lang"))
	hint := strings.TrimSpace(q.Get("hint"))
	if hint == "" {
		if al := strings.TrimSpace(r.Header.Get("Accept-Language")); al != "" {
			hint = strings.TrimSpace(strings.Split(strings.Split(al, ",")[0], ";")[0])
		}
	}
	stored := lang
	if lang == "" {
		cfg := LoadConfig(s.homeDir)
		stored = cfgString(cfg, "ui_language", i18nAuto)
	}
	writeJSON(w, http.StatusOK, catalogPayload(stored, hint))
}
