package cli

import (
	"regexp"
	"strings"
)

// DangerousCommand is the hard gate `remedy exec` (and `remedy skill run`)
// put in front of every spawn. It returns why argv must not run, or "".
//
// The Zig spawn policy already refuses the privilege basenames (sudo, dd,
// format, ...); this adds the whole-command patterns the CLI always blocked:
// recursive wipes of / or ~, pipes into a shell, device writes, encoded
// PowerShell, download-and-drop, privilege tools hidden in `bash -c` or
// `pwsh -Command`, and commands that would kill Remedy itself.
func DangerousCommand(argv []string) string {
	if len(argv) == 0 {
		return ""
	}
	base := guardBase(argv[0])
	if base == "chmod" {
		if chmodPlusX(argv) {
			return ""
		}
		return "Dangerous command: chmod"
	}
	if dangerousBasenames[base] {
		return "Dangerous command: " + base
	}

	full := strings.Join(argv, " ")
	fullL := strings.ToLower(full)
	for _, p := range hardDangerousPatterns {
		if p.re.MatchString(fullL) {
			return p.reason + ": " + clipRunes(fullL, 100)
		}
	}

	if shellWrappers[base] || nestedPrivilegeRE.MatchString(full) {
		if nestedPrivilegeRE.MatchString(full) {
			return "Dangerous nested command: " + clipRunes(full, 100)
		}
		for _, payload := range innerShellPayloads(argv) {
			if nestedPrivilegeRE.MatchString(payload) {
				return "Dangerous nested command: " + clipRunes(payload, 100)
			}
			fields := strings.Fields(payload)
			first := ""
			if len(fields) > 0 {
				first = fields[0]
			}
			d := guardBase(first)
			if !dangerousBasenames[d] {
				continue
			}
			if d == "chmod" && chmodPlusX(fields) {
				continue
			}
			return "Dangerous nested command: " + d
		}
	}

	return hostSelfKill(full)
}

func clipRunes(s string, n int) string {
	r := []rune(s)
	if len(r) > n {
		return string(r[:n])
	}
	return s
}

// guardBase is the lowercased file name of token without a trailing .exe.
func guardBase(token string) string {
	if i := strings.LastIndexAny(token, `/\`); i >= 0 {
		token = token[i+1:]
	}
	b := strings.ToLower(token)
	return strings.TrimSuffix(b, ".exe")
}

var dangerousBasenames = setOf(
	// Unix privilege / disk
	"sudo", "su", "chmod", "chown", "mkfs", "dd", "fdisk",
	"passwd", "useradd", "usermod", "groupadd",
	// Windows system / privilege
	"reg", "takeown", "icacls", "net", "wmic", "sc", "schtasks",
	"vssadmin", "bcdedit", "wevtutil", "diskpart", "cipher",
	"format", "shutdown", "reboot",
)

var shellWrappers = setOf("bash", "sh", "zsh", "dash", "cmd", "powershell", "pwsh",
	"env", "nice", "nohup", "sudo", "doas", "xargs")

var chmodPlusXModes = setOf("+x", "a+x", "u+x", "ug+x", "ugo+x", "a+rx", "u+rx",
	"755", "0755", "744", "0744")

func setOf(xs ...string) map[string]bool {
	m := make(map[string]bool, len(xs))
	for _, x := range xs {
		m[x] = true
	}
	return m
}

// chmodPlusX allows `chmod +x rel/path` inside the project: making a script
// executable is not privilege, chmod of anything absolute or above is.
func chmodPlusX(argv []string) bool {
	if len(argv) < 3 || !chmodPlusXModes[strings.TrimSpace(argv[1])] {
		return false
	}
	for _, p := range argv[2:] {
		if !chmodPathOK(p) {
			return false
		}
	}
	return true
}

func chmodPathOK(path string) bool {
	p := strings.Trim(strings.TrimSpace(path), `"'`)
	if p == "" || strings.HasPrefix(p, "/") || strings.HasPrefix(p, "~") {
		return false
	}
	if len(p) >= 2 && p[1] == ':' {
		return false
	}
	for _, part := range strings.FieldsFunc(p, func(r rune) bool { return r == '/' || r == '\\' }) {
		if part == ".." {
			return false
		}
	}
	return true
}

// innerShellPayloads pulls the script out of -c / -Command / /c style args.
func innerShellPayloads(argv []string) []string {
	var out []string
	for i := 1; i < len(argv); i++ {
		arg := strings.TrimSpace(argv[i])
		low := strings.ToLower(arg)
		switch low {
		case "-c", "/c", "/k", "-command", "-encodedcommand", "-enc", "-e", "-ec":
			if i+1 < len(argv) {
				out = append(out, argv[i+1])
				i++
				continue
			}
		}
		if strings.HasPrefix(low, "-command:") || strings.HasPrefix(low, "-c:") {
			out = append(out, arg[strings.Index(arg, ":")+1:])
		}
	}
	return out
}

type guardPattern struct {
	re     *regexp.Regexp
	reason string
}

func patterns(pairs ...string) []guardPattern {
	out := make([]guardPattern, 0, len(pairs)/2)
	for i := 0; i+1 < len(pairs); i += 2 {
		out = append(out, guardPattern{regexp.MustCompile("(?i)" + pairs[i]), pairs[i+1]})
	}
	return out
}

var hardDangerousPatterns = patterns(
	`(^|[\s;&|])format(\s|$)`, "Filesystem format",
	`(^|[\s;&|])shutdown(\s|$)`, "System shutdown",
	`(^|[\s;&|])reboot(\s|$)`, "System reboot",
	`rm\s+(-[a-zA-Z]*r[a-zA-Z]*f[a-zA-Z]*|-[a-zA-Z]*f[a-zA-Z]*r[a-zA-Z]*)\s+(/|~|\$home)`, "Recursive delete of system path",
	`rm\s+(-[a-zA-Z]*f[a-zA-Z]*\s+)*(/|~|\$home|c:\\)`, "Recursive delete of system path",
	`del\s+/[fqs]+\s+`, "Windows forced recursive delete",
	`rmdir\s+/s(\s+/q)?\s+`, "Windows recursive rmdir",
	`rd\s+/s(\s+/q)?\s+`, "Windows recursive rd",
	`\|\s*(sh|bash|pwsh|powershell|cmd)(\s|$)`, "Shell pipe injection",
	`>\s*/dev/`, "Device write",
	`invoke-expression|iex\s+`, "PowerShell Invoke-Expression",
	`remove-item\s+.*-recurse`, "PowerShell recursive delete",
	`(^|[\s;&|])(rm|del|erase)\s+(-[a-zA-Z]*[rf][a-zA-Z]*\s+)+`, "Forced file deletion",
	`(?:^|[\s;&|])-(?:encodedcommand|enc|ec)\b`, "PowerShell EncodedCommand",
	`(?:powershell|pwsh)(?:\.exe)?(?:\s+[/\-\w]+)*\s+-e(?:\s|$|=)`, "PowerShell EncodedCommand (-e)",
	`\badd-type\b[^\n]*-typedefinition\b`, "PowerShell Add-Type injection",
	`\bcertutil\b[^\n]*-urlcache\b`, "certutil URL cache download",
	`\b(?:system\.)?net\.webclient\b[^\n]*download`, "WebClient download",
	`\bdownloadfile\s*\(`, "DownloadFile invoke",
)

var nestedPrivilegeRE = regexp.MustCompile(`(?i)(?:^|[\s;&|` + "`" + `(]|(?:&&|\|\|)\s*)(?:` +
	`reg(?:\.exe)?\s+(?:add|delete|import|save|load|unload|copy|export|query\s+/s)|` +
	`takeown(?:\.exe)?\b|` +
	`icacls(?:\.exe)?\b|` +
	`net(?:\.exe)?\s+(?:user|localgroup|accounts)\b|` +
	`net(?:\.exe)?\s+share\b|` +
	`wmic(?:\.exe)?\b|` +
	`sc(?:\.exe)?\s+(?:create|delete|config|start|stop|failure)\b|` +
	`schtasks(?:\.exe)?\s+/create\b|` +
	`vssadmin(?:\.exe)?\b|` +
	`bcdedit(?:\.exe)?\b|` +
	`wevtutil(?:\.exe)?\b|` +
	`diskpart(?:\.exe)?\b|` +
	`cipher(?:\.exe)?\s+/w\b|` +
	`format(?:\.exe)?\s+[a-z]:|` +
	`shutdown(?:\.exe)?\b|` +
	`reboot\b|` +
	`mkfs(?:\.\w+)?\b|` +
	`\bdd\b\s+if=|` +
	`fdisk\b|` +
	`useradd\b|usermod\b|groupadd\b|passwd\b|` +
	`chmod\s+[0-7]{3,4}\s+/|chown\s+\S+\s+/` +
	`)`)

var selfKillAlwaysBlock = patterns(
	`taskkill\s+[^\n]*/im\s+remedy(\.exe)?`, "Killing remedy.exe (host API)",
	`stop-process[^\n]*-name\s+['"]?remedy(\.exe)?['"]?`, "Stopping process named remedy (host API)",
	`get-process\s+[^\n]*remedy[^\n]*stop-process|stop-process[^\n]*get-process[^\n]*remedy`, "Pipeline stop of remedy process",
	`(localport|local.?port)\s*[:=]?\s*7400[^\n]{0,200}(stop-process|taskkill|kill)`, "Killing process on port 7400 (Remedy API)",
	`(stop-process|taskkill|kill)[^\n]{0,200}(localport|local.?port)\s*[:=]?\s*7400`, "Killing process on port 7400 (Remedy API)",
	`remedy_serve\.lock|remedy serve.*stop|stop[^\n]*remedy serve`, "Stopping remedy serve",
)

var selfKillAppPatterns = patterns(
	`taskkill\s+[^\n]*/im\s+app(\.exe)?`, "taskkill /IM app.exe kills ALL Tauri apps including Remedy Desktop",
	`stop-process\s+[^\n]*-name\s+['"]?app(\.exe)?['"]?`, "Stop-Process -Name app kills Remedy Desktop (same default Tauri name)",
	`get-process\s+['"]?app(\.exe)?['"]?[^\n]{0,80}stop-process`, "Get-Process app | Stop-Process kills Remedy Desktop",
	`get-process\s+[^\n]*\|\s*[^\n]*stop-process[^\n]*\bapp\b`, "Filtered Stop-Process still targeting bare app name",
	`stop-process\s+[^\n]*\(?(get-process\s+app)`, "Stop-Process (Get-Process app) kills Remedy Desktop",
	`(stop-process|taskkill)[^\n]{0,120}(processname\s*-eq\s*['"]app['"]|\.name\s*-eq\s*['"]app['"])`, "Stopping all processes named app (includes Remedy Desktop)",
)

var selfKillProjectScopeRE = regexp.MustCompile(`(?i)(` +
	`secretfolder|secretsticky|remedyai[/\\]desktop` +
	`|commandline\s*-match|path\s*-match|\.path\s*-match` +
	`|where-object[^\n]{0,80}(secretfolder|secretsticky|project)` +
	`|filter\s+[^\n]{0,40}(secretfolder|secretsticky)` +
	`)`)

// hostSelfKill blocks commands that would stop Remedy's own API or Desktop.
func hostSelfKill(full string) string {
	if strings.TrimSpace(full) == "" {
		return ""
	}
	for _, p := range selfKillAlwaysBlock {
		if p.re.MatchString(full) {
			return p.reason + ". Do not stop the host agent. " +
				"Target only the project app by full Path/CommandLine filter " +
				"(e.g. SecretFolder), never bare app.exe or port 7400."
		}
	}
	if selfKillProjectScopeRE.MatchString(full) {
		return ""
	}
	for _, p := range selfKillAppPatterns {
		if p.re.MatchString(full) {
			return p.reason + ". Use a Path/CommandLine filter for the project " +
				`(e.g. Where-Object { $_.Path -match "SecretFolder" }) ` +
				"— never Get-Process app | Stop-Process."
		}
	}
	return ""
}
