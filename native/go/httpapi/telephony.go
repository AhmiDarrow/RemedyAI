package httpapi

import (
	"encoding/json"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"runtime"
	"sort"
	"strconv"
	"strings"
	"time"
)

const telephonyTermsVersion = 1

const telephonyTermsDoc = "docs/TELEPHONY_TERMS.md"

var telephonyTermsChanges = map[int]string{
	1: "the first version of the phone terms",
}

var telephonySpokenPoints = []string{
	"I am not an emergency service — never use me to call 911 or any emergency " +
		"number, and keep a phone that works without me.",
	"I can get things wrong on a call, so check anything that matters. There is " +
		"no warranty and no liability beyond the licence.",
	"Recording and AI-disclosure rules differ by country and by state, and " +
		"following them is your call. I disclose that I am an assistant by default.",
	"Phone service and any calling app are your accounts with those companies, " +
		"at their prices and under their terms.",
	"I will not claim to be human if asked, read out card numbers or one-time " +
		"codes, or agree to a payment without you.",
}

type telephonyConsent struct {
	Version int    `json:"version"`
	At      string `json:"at"`
	Doc     string `json:"doc,omitempty"`
}

func telephonyDir(homeDir string) string {
	return filepath.Join(ResolveHomeDir(homeDir), "telephony")
}

func telephonyConsentPath(homeDir string) string {
	return filepath.Join(telephonyDir(homeDir), "consent.json")
}

func telephonyLinePath(homeDir string) string {
	return filepath.Join(telephonyDir(homeDir), "line.json")
}

func readTelephonyConsent(homeDir string) telephonyConsent {
	var c telephonyConsent
	raw, err := os.ReadFile(telephonyConsentPath(homeDir))
	if err != nil {
		return c
	}
	if json.Unmarshal(raw, &c) != nil {
		return telephonyConsent{}
	}
	return c
}

func (c telephonyConsent) current() bool {
	return c.Version >= telephonyTermsVersion
}

func (c telephonyConsent) stale() bool {
	return c.Version > 0 && c.Version < telephonyTermsVersion
}

func acceptTelephonyConsent(homeDir string) (telephonyConsent, error) {
	c := telephonyConsent{
		Version: telephonyTermsVersion,
		At:      time.Now().UTC().Format(time.RFC3339Nano),
		Doc:     telephonyTermsDoc,
	}
	if err := os.MkdirAll(telephonyDir(homeDir), 0o700); err != nil {
		return c, err
	}
	if err := writeJSONAtomic(telephonyConsentPath(homeDir), c); err != nil {
		return c, err
	}
	return c, nil
}

func withdrawTelephonyConsent(homeDir string) {
	_ = os.Remove(telephonyConsentPath(homeDir))
}

func telephonyAsk(homeDir string) string {
	c := readTelephonyConsent(homeDir)
	if c.current() {
		return ""
	}
	lead := "Before I can use a phone, a few things you should hear."
	if c.stale() {
		changed := telephonyTermsChanges[telephonyTermsVersion]
		if changed == "" {
			changed = "the phone terms have changed"
		}
		lead = "The phone terms have changed — " + changed + ". Worth hearing again."
	}
	var b strings.Builder
	b.WriteString(lead)
	b.WriteByte('\n')
	for _, p := range telephonySpokenPoints {
		b.WriteString("- ")
		b.WriteString(p)
		b.WriteByte('\n')
	}
	b.WriteString("The full version is in ")
	b.WriteString(telephonyTermsDoc)
	b.WriteString(". Are you happy for me to go ahead?")
	return b.String()
}

func chosenTelephonyLine(homeDir string) string {
	raw, err := os.ReadFile(telephonyLinePath(homeDir))
	if err != nil {
		return ""
	}
	var parsed map[string]any
	if json.Unmarshal(raw, &parsed) != nil || parsed == nil {
		return ""
	}
	return strings.TrimSpace(anyString(parsed["line"]))
}

func chooseTelephonyLine(homeDir, name string) (string, error) {
	if err := os.MkdirAll(telephonyDir(homeDir), 0o700); err != nil {
		return "", err
	}
	if err := writeJSONAtomic(telephonyLinePath(homeDir), map[string]any{"line": name}); err != nil {
		return "", err
	}
	return name, nil
}

type telephonyLineOption struct {
	Name        string
	Title       string
	Summary     string
	Cost        string
	Catch       string
	Ready       bool
	Achievable  bool
	Missing     []string
	Action      string
	Standalone  bool
}

func (o telephonyLineOption) public() map[string]any {
	missing := o.Missing
	if missing == nil {
		missing = []string{}
	}
	return map[string]any{
		"name": o.Name, "title": o.Title, "summary": o.Summary,
		"cost": o.Cost, "catch": o.Catch, "ready": o.Ready,
		"achievable": o.Achievable, "missing": missing,
		"action": o.Action, "standalone": o.Standalone,
	}
}

func lookPathQuiet(names ...string) string {
	pathEnv := os.Getenv("PATH")
	sep := string(os.PathListSeparator)
	for _, dir := range strings.Split(pathEnv, sep) {
		dir = strings.TrimSpace(dir)
		if dir == "" {
			continue
		}
		for _, name := range names {
			p := filepath.Join(dir, name)
			if fileExists(p) {
				return p
			}
		}
	}
	return ""
}

func telephonyHasADB() bool {
	if lookPathQuiet("adb", "adb.exe") != "" {
		return true
	}
	return false
}

func telephonyHasBaresip(homeDir string) bool {
	if lookPathQuiet("baresip", "baresip.exe") != "" {
		return true
	}
	home := ResolveHomeDir(homeDir)
	for _, name := range []string{"baresip.exe", "baresip"} {
		if fileExists(filepath.Join(home, "bin", name)) {
			return true
		}
	}
	return false
}

func telephonyLineOptions(homeDir string) []telephonyLineOption {
	sipMissing := []string{}
	sipAction := ""
	if !telephonyHasBaresip(homeDir) {
		sipMissing = []string{"the SIP engine is not installed"}
		sipAction = "I can fetch it when you want me to have my own number."
	} else {
		sipMissing = []string{"no SIP account is configured"}
		sipAction = "You would need a trunk provider account — a number costs a dollar or two a month."
	}

	adbOK := telephonyHasADB()
	vmMissing := []string{}
	vmAction := ""
	if !adbOK {
		vmMissing = []string{"the Android platform tools are not installed"}
		vmAction = "I can install them — a small free download, and no phone is involved."
	} else {
		vmMissing = []string{"there is no Android VM running on this PC yet"}
		vmAction = "I can set one up — it runs here, so distance and phone battery stop mattering."
	}

	wiredMissing := []string{}
	wiredAction := ""
	if !adbOK {
		wiredMissing = []string{"the Android platform tools are not installed"}
		wiredAction = "I can install them for you — they are a small free download from Google."
	} else {
		wiredMissing = []string{"no phone is connected with USB debugging turned on"}
		wiredAction = "On the phone: Settings - About phone - tap Build number seven times, " +
			"then Settings - System - Developer options - USB debugging. " +
			"Plug it in and I will check again."
	}

	btAchievable := true
	btMissing := []string{"I could not tell whether this PC has Bluetooth"}
	btAction := "Worth checking Settings - Bluetooth & devices before we rely on it."
	if runtime.GOOS != "windows" {
		btAchievable = false
		btMissing = []string{"the phone bridge needs Windows for now"}
		btAction = ""
	}

	return []telephonyLineOption{
		{
			Name: "sip", Title: "A number of my own",
			Summary: "I get my own phone line, and your number can forward to it.",
			Cost: "a dollar or two a month, plus pennies a minute",
			Catch: "It works even if your phone is off, lost, or in another room.",
			Ready: len(sipMissing) == 0, Achievable: true, Missing: sipMissing,
			Action: sipAction, Standalone: true,
		},
		{
			Name: "vm_voip", Title: "A calling app on this PC",
			Summary: "I run Android here on your machine and call through an app such as Google Voice.",
			Cost: "nothing recurring",
			Catch: "No phone involved and nothing to stay in range of, but the " +
				"number belongs to a cloud service rather than to you.",
			Ready: len(vmMissing) == 0, Achievable: true, Missing: vmMissing,
			Action: vmAction, Standalone: true,
		},
		{
			Name: "phone_wired", Title: "Your own phone, on a cable",
			Summary: "Your real number and SIM, with a cable carrying the sound.",
			Cost: "about ten dollars once, for the adapter",
			Catch: "It is genuinely your number, but the phone has to stay plugged in here.",
			Ready: len(wiredMissing) == 0, Achievable: true, Missing: wiredMissing,
			Action: wiredAction, Standalone: false,
		},
		{
			Name: "bluetooth_hfp", Title: "Your own phone, wirelessly",
			Summary: "The same as the cable, without the cable.",
			Cost: "nothing, if this PC has Bluetooth",
			Catch: "The one option that can drop out: the phone has to stay within " +
				"a few metres and charged, so I would not rely on it.",
			Ready: len(btMissing) == 0, Achievable: btAchievable, Missing: btMissing,
			Action: btAction, Standalone: false,
		},
	}
}

func offerTelephonyLines(opts []telephonyLineOption, recommend string) string {
	usable := make([]telephonyLineOption, 0, len(opts))
	for _, o := range opts {
		if o.Achievable {
			usable = append(usable, o)
		}
	}
	if len(usable) == 0 {
		return "I cannot find any way to get a phone line on this machine yet."
	}
	// standalone first; within that, ready before missing.
	ordered := append([]telephonyLineOption(nil), usable...)
	sort.SliceStable(ordered, func(i, j int) bool {
		ai, aj := 0, 0
		if !ordered[i].Standalone {
			ai += 2
		}
		if !ordered[j].Standalone {
			aj += 2
		}
		if len(ordered[i].Missing) > 0 {
			ai++
		}
		if len(ordered[j].Missing) > 0 {
			aj++
		}
		return ai < aj
	})
	var head string
	switch len(ordered) {
	case 1:
		head = "There is one way"
	case 2:
		head = "There are two ways"
	case 3:
		head = "There are three ways"
	case 4:
		head = "There are four ways"
	default:
		head = "There are " + strconv.Itoa(len(ordered)) + " ways"
	}
	var b strings.Builder
	b.WriteString(head)
	b.WriteString(" I can get a phone line.\n")
	for i, o := range ordered {
		mark := ""
		if recommend != "" && o.Name == recommend {
			mark = " (my suggestion)"
		} else if o.Ready {
			mark = " (ready now)"
		}
		b.WriteString(strconv.Itoa(i + 1))
		b.WriteString(") ")
		b.WriteString(o.Title)
		b.WriteString(" — ")
		b.WriteString(o.Summary)
		b.WriteString(" Cost: ")
		b.WriteString(o.Cost)
		b.WriteString(". ")
		b.WriteString(o.Catch)
		if len(o.Missing) > 0 {
			b.WriteString(" To use it: ")
			if o.Action != "" {
				b.WriteString(o.Action)
			} else {
				b.WriteString(o.Missing[0])
			}
		}
		b.WriteString(mark)
		b.WriteByte('\n')
	}
	b.WriteString("Which would you like me to set up?")
	return b.String()
}

func (s *Server) telephonyStatusPayload() map[string]any {
	home := ResolveHomeDir(s.homeDir)
	c := readTelephonyConsent(home)
	opts := telephonyLineOptions(home)
	lines := make([]map[string]any, 0, len(opts))
	for _, o := range opts {
		lines = append(lines, o.public())
	}
	return map[string]any{
		"terms": map[string]any{
			"agreed":          c.current(),
			"stale":           c.stale(),
			"version":         c.Version,
			"current_version": telephonyTermsVersion,
			"ask":             telephonyAsk(home),
		},
		"chosen":    chosenTelephonyLine(home),
		"offer":     offerTelephonyLines(opts, "sip"),
		"lines":     lines,
		"real_line": false,
		"phase":     0,
		"loopback":  true,
		"message": "Calling a real number is not on this computer yet. " +
			"The voice and turn-taking are ready; a loopback line can " +
			"exercise them without dialling anyone. A SIP trunk is next.",
	}
}

func (s *Server) handleTelephonyStatus(w http.ResponseWriter, _ *http.Request) {
	writeJSON(w, http.StatusOK, s.telephonyStatusPayload())
}

func (s *Server) handleTelephonyTerms(w http.ResponseWriter, r *http.Request) {
	var body struct {
		Accept *bool `json:"accept"`
	}
	_ = json.NewDecoder(io.LimitReader(r.Body, 1<<20)).Decode(&body)
	accept := true
	if body.Accept != nil {
		accept = *body.Accept
	}
	home := ResolveHomeDir(s.homeDir)
	if accept {
		c, err := acceptTelephonyConsent(home)
		if err != nil {
			writeJSON(w, http.StatusInternalServerError, map[string]any{
				"ok": false, "error": err.Error(),
			})
			return
		}
		writeJSON(w, http.StatusOK, map[string]any{
			"ok": true, "agreed": true, "version": c.Version, "at": c.At,
		})
		return
	}
	withdrawTelephonyConsent(home)
	writeJSON(w, http.StatusOK, map[string]any{"ok": true, "agreed": false})
}

func (s *Server) handleTelephonyChoose(w http.ResponseWriter, r *http.Request) {
	var body struct {
		Name string `json:"name"`
	}
	if err := json.NewDecoder(io.LimitReader(r.Body, 1<<20)).Decode(&body); err != nil && err != io.EOF {
		writeJSON(w, http.StatusBadRequest, map[string]any{"detail": "invalid JSON body"})
		return
	}
	home := ResolveHomeDir(s.homeDir)
	wanted := strings.ToLower(strings.TrimSpace(body.Name))
	known := map[string]struct{}{}
	var names []string
	for _, o := range telephonyLineOptions(home) {
		if o.Achievable {
			known[o.Name] = struct{}{}
			names = append(names, o.Name)
		}
	}
	if _, ok := known[wanted]; !ok {
		avail := strings.Join(names, ", ")
		if avail == "" {
			avail = "none on this PC"
		}
		writeJSON(w, http.StatusOK, map[string]any{
			"ok":    false,
			"error": "No line called '" + wanted + "' on this computer. Available: " + avail + ".",
			"available": names,
		})
		return
	}
	name, err := chooseTelephonyLine(home, wanted)
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]any{
			"ok": false, "error": err.Error(),
		})
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"ok": true, "chosen": name})
}
