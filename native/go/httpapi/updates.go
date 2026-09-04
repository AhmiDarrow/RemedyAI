package httpapi

import (
	"encoding/json"
	"io"
	"net/http"
	"strings"
	"time"
)

type updatesCacheEntry struct {
	at   time.Time
	body map[string]any
}

// updatesHTTPGet fetches JSON from url (tests may replace).
var updatesHTTPGet = defaultUpdatesHTTPGet

func defaultUpdatesHTTPGet(url string, timeout time.Duration) (map[string]any, error) {
	client := &http.Client{Timeout: timeout}
	req, err := http.NewRequest(http.MethodGet, url, nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set("Accept", "application/json")
	req.Header.Set("User-Agent", "Remedy-Updater")
	resp, err := client.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	body, err := io.ReadAll(io.LimitReader(resp.Body, 2<<20))
	if err != nil {
		return nil, err
	}
	var payload map[string]any
	if err := json.Unmarshal(body, &payload); err != nil {
		return nil, err
	}
	return payload, nil
}

func (s *Server) handleUpdatesCheck(w http.ResponseWriter, r *http.Request) {
	writeJSON(w, http.StatusOK, s.checkUpdates(r.URL.Query().Get("current")))
}

func (s *Server) checkUpdates(currentRaw string) map[string]any {
	pythonVersion := s.version
	currentNorm := strings.TrimSpace(strings.TrimLeft(strings.TrimSpace(currentRaw), "vV"))
	if currentNorm == "" {
		currentNorm = pythonVersion
	}

	now := time.Now()
	s.updatesMu.Lock()
	if s.updatesCache == nil {
		s.updatesCache = map[string]updatesCacheEntry{}
	}
	if hit, ok := s.updatesCache[currentNorm]; ok && now.Sub(hit.at) < 5*time.Minute && hit.body != nil {
		out := cloneMap(hit.body)
		s.updatesMu.Unlock()
		return out
	}
	s.updatesMu.Unlock()

	var latestPython, latestDesktop, releaseURL, installerURL any
	var errors []string

	if data, err := updatesHTTPGet("https://pypi.org/pypi/remedy-ai/json", 10*time.Second); err != nil {
		errors = append(errors, "PyPI: "+err.Error())
	} else if info, ok := asStringMap(data["info"]); ok {
		if v := strings.TrimSpace(fmtString(info["version"])); v != "" {
			latestPython = v
		}
	}

	for _, url := range []string{
		"https://github.com/AhmiDarrow/RemedyAI/releases/latest/download/latest.json",
		"https://api.github.com/repos/AhmiDarrow/RemedyAI/releases/latest",
	} {
		data, err := updatesHTTPGet(url, 15*time.Second)
		if err != nil {
			tail := url
			if i := strings.LastIndex(url, "/"); i >= 0 {
				tail = url[i+1:]
			}
			errors = append(errors, "GitHub ("+tail+"): "+err.Error())
			continue
		}
		if v := strings.TrimSpace(fmtString(data["version"])); v != "" {
			latestDesktop = strings.TrimLeft(v, "vV")
			releaseURL = "https://github.com/AhmiDarrow/RemedyAI/releases/latest"
			installerURL = installerURLFromLatestJSON(data)
			break
		}
		if tag := strings.TrimSpace(fmtString(data["tag_name"])); tag != "" {
			latestDesktop = strings.TrimLeft(tag, "vV")
			if html := strings.TrimSpace(fmtString(data["html_url"])); html != "" {
				releaseURL = html
			} else {
				releaseURL = "https://github.com/AhmiDarrow/RemedyAI/releases/latest"
			}
			installerURL = installerURLFromGitHubRelease(data)
			break
		}
	}

	updateAvailable := false
	if latestDesktop != nil && compareVersions(fmtString(latestDesktop), currentNorm) > 0 {
		updateAvailable = true
	} else if latestPython != nil && latestDesktop == nil &&
		compareVersions(fmtString(latestPython), currentNorm) > 0 {
		updateAvailable = true
	}
	if updateAvailable && latestDesktop != nil && strings.TrimSpace(fmtString(installerURL)) == "" {
		errors = append(errors,
			"Newer desktop release found but no Windows installer URL on the release.")
	}

	var errOut any
	if len(errors) > 0 {
		errOut = strings.Join(errors, " · ")
	}
	result := map[string]any{
		"current_version":   currentNorm,
		"python_version":    pythonVersion,
		"latest_python":     latestPython,
		"latest_desktop":    latestDesktop,
		"release_url":       releaseURL,
		"installer_url":     installerURL,
		"update_available":  updateAvailable,
		"error":             errOut,
	}

	s.updatesMu.Lock()
	s.updatesCache[currentNorm] = updatesCacheEntry{at: now, body: cloneMap(result)}
	s.updatesMu.Unlock()
	return result
}

func installerURLFromLatestJSON(data map[string]any) any {
	if platforms, ok := asStringMap(data["platforms"]); ok {
		if win, ok := asStringMap(platforms["windows-x86_64"]); ok {
			if u := strings.TrimSpace(fmtString(win["url"])); u != "" {
				return u
			}
		}
	}
	if u := strings.TrimSpace(fmtString(data["url"])); u != "" {
		return u
	}
	return nil
}

func installerURLFromGitHubRelease(data map[string]any) any {
	assets, _ := data["assets"].([]any)
	for _, a := range assets {
		m, ok := asStringMap(a)
		if !ok {
			continue
		}
		name := fmtString(m["name"])
		lower := strings.ToLower(name)
		if strings.HasSuffix(name, "-setup.exe") || strings.HasSuffix(name, "_x64-setup.exe") ||
			(strings.HasSuffix(name, ".exe") && (strings.Contains(lower, "setup") || strings.Contains(lower, "remedy"))) {
			if u := strings.TrimSpace(fmtString(m["browser_download_url"])); u != "" {
				return u
			}
		}
	}
	return nil
}
