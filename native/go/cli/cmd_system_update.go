package cli

import (
	"encoding/json"
	"io"
	"net/http"
	"os"
	"strings"
	"time"

	"github.com/AhmiDarrow/RemedyAI/native/go/httpapi"
)

// `remedy update [--check]`: compares the running version with the release
// feed the desktop updater reads and says how to update. It never changes
// the installation: the desktop app updates itself, and a command-line
// install is updated by running the install script again.

// sysLatestURL is the release feed (latest.json, field "version").
var sysLatestURL = "https://github.com/AhmiDarrow/RemedyAI/releases/latest/download/latest.json"

// sysUpdater holds every outside effect of `remedy update`, so tests drive
// it without a network.
type sysUpdater struct {
	installed func() string
	latest    func() string // "" when unreachable or malformed
	desktop   func() bool
}

var sysNewUpdater = func(c *Ctx) *sysUpdater {
	return &sysUpdater{
		installed: func() string { return Version(httpapi.Version) },
		latest:    func() string { return sysFetchLatest(sysLatestURL) },
		desktop:   sysDesktopInstall,
	}
}

// cmdUpdate has no apply step, so --check changes nothing.
func cmdUpdate(c *Ctx) int { return sysNewUpdater(c).runUpdate(c) }

// sysDesktopInstall reports a runtime started by the desktop app.
func sysDesktopInstall() bool {
	for _, k := range []string{"REMEDY_DESKTOP", "REMEDY_DESKTOP_SIDECAR"} {
		switch strings.ToLower(strings.TrimSpace(os.Getenv(k))) {
		case "1", "true", "yes", "on":
			return true
		}
	}
	return false
}

// sysFetchLatest reads "version" from the release feed; "" on any failure
// or a malformed answer.
func sysFetchLatest(url string) string {
	req, err := http.NewRequest(http.MethodGet, url, nil)
	if err != nil {
		return ""
	}
	req.Header.Set("Accept", "application/json")
	req.Header.Set("User-Agent", "Remedy-Updater")
	resp, err := (&http.Client{Timeout: 10 * time.Second}).Do(req)
	if err != nil {
		return ""
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return ""
	}
	var data struct {
		Version any `json:"version"`
	}
	if json.NewDecoder(io.LimitReader(resp.Body, 8<<20)).Decode(&data) != nil {
		return ""
	}
	v, _ := data.Version.(string)
	return strings.TrimSpace(v)
}

// sysParseVersion: strip v, drop -/+ suffixes, leading digits of each dot
// segment (missing = 0), padded to three parts.
func sysParseVersion(v string) []int {
	s := strings.TrimLeft(strings.TrimSpace(v), "vV")
	for _, sep := range []string{"-", "+"} {
		if i := strings.Index(s, sep); i >= 0 {
			s = s[:i]
		}
	}
	var parts []int
	for _, p := range strings.Split(s, ".") {
		n := 0
		for _, ch := range p {
			if ch < '0' || ch > '9' {
				break
			}
			n = n*10 + int(ch-'0')
		}
		parts = append(parts, n)
	}
	for len(parts) < 3 {
		parts = append(parts, 0)
	}
	return parts
}

// sysIsNewer is tuple ordering (a longer tuple with an equal prefix sorts
// higher).
func sysIsNewer(latest, installed string) bool {
	a, b := sysParseVersion(latest), sysParseVersion(installed)
	for i := 0; i < len(a) && i < len(b); i++ {
		if a[i] != b[i] {
			return a[i] > b[i]
		}
	}
	return len(a) > len(b)
}

func (u *sysUpdater) runUpdate(c *Ctx) int {
	sysPanel(c, "Remedy Updater", "Checking for updates...")
	installed := u.installed()
	if installed == "" {
		installed = "unknown"
	}
	c.Printf("  Installed: %s\n", installed)

	c.Printf("  Checking the release feed... ")
	latest := u.latest()
	if latest == "" {
		c.Println("unreachable (offline?)")
		c.Errorf("\nCould not reach the release feed; update status unknown (offline?).\n")
		return 1
	}
	c.Println(latest)

	switch {
	case sysIsNewer(latest, installed):
		c.Printf("\nUpdate available: %s -> %s\n", installed, latest)
		if u.desktop() {
			c.Println("The desktop app updates itself: About Remedy -> Check for updates.")
		} else {
			c.Println("Run the install script again (scripts/install.sh, or scripts/install.ps1 on Windows).")
			c.Println("If you use the desktop app, it updates itself: About Remedy -> Check for updates.")
		}
	case sysIsNewer(installed, latest):
		c.Printf("\nThe latest release is %s (installed: %s is newer or different)\n", latest, installed)
	default:
		c.Println("\nRemedy is up to date.")
	}
	return 0
}
