package cli

import (
	"context"
	"errors"
	"net"
	"os"
	"os/signal"
	"path/filepath"
	"runtime"
	"time"

	"github.com/AhmiDarrow/RemedyAI/native/go/tools"
)

const releasesURL = "https://github.com/AhmiDarrow/RemedyAI/releases"

// findDesktopDir looks for desktop/package.json from the working directory
// upward (a source checkout), then under REMEDY_DEV_ROOT.
func findDesktopDir() string {
	try := func(dir string) string {
		cand := filepath.Join(dir, "desktop")
		if st, err := os.Stat(filepath.Join(cand, "package.json")); err == nil && !st.IsDir() {
			return cand
		}
		return ""
	}
	if root := os.Getenv("REMEDY_DEV_ROOT"); root != "" {
		if d := try(expandUser(root)); d != "" {
			return d
		}
	}
	cur, err := os.Getwd()
	if err != nil {
		return ""
	}
	for i := 0; i < 6; i++ {
		if d := try(cur); d != "" {
			return d
		}
		parent := filepath.Dir(cur)
		if parent == cur {
			break
		}
		cur = parent
	}
	return ""
}

// lookNpm is replaceable in tests.
var lookNpm = func() string {
	for _, name := range []string{"npm", "pnpm", "yarn"} {
		if p, err := tools.ResolveArgv0(name, ""); err == nil {
			return p
		}
	}
	return ""
}

// runChild runs argv attached to this terminal; replaceable in tests.
var runChild = func(argv []string, cwd string, c *Ctx) int {
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt)
	defer stop()
	code, _, err := tools.RunAttached(ctx, argv, cwd, nil, c.In, c.Out, c.Err, 0)
	if err != nil {
		c.Errorf("%v\n", err)
		if code == 0 {
			code = 1
		}
	}
	return code
}

func cmdDesktop(c *Ctx) int {
	sub := c.P.Sub(1)
	switch sub {
	case "launch":
		return desktopLaunch(c)
	case "status":
		return desktopStatus(c)
	}
	dir := findDesktopDir()
	if dir == "" {
		c.Errorf("Desktop directory not found (looked for desktop/package.json from here upward).\n")
		c.Errorf("Run this from a RemedyAI checkout, or set REMEDY_DEV_ROOT.\n")
		return 1
	}
	npm := lookNpm()
	if npm == "" {
		c.Errorf("No Node package manager found (npm/pnpm/yarn).\nInstall Node.js from https://nodejs.org\n")
		return 1
	}
	switch sub {
	case "install":
		c.Println("Installing desktop dependencies...")
		if code := runChild([]string{npm, "install"}, dir, c); code != 0 {
			c.Errorf("npm install failed. Is Node.js installed?\n")
			return code
		}
		c.Println("Desktop dependencies installed.")
		c.Println("Run 'remedy desktop dev' to start, then open http://localhost:5173")
	case "dev":
		c.Println("Starting desktop dev server...")
		c.Println("Make sure 'remedy serve' (remedy-runtime) is up, or use tauri:dev.")
		c.Println("Open http://localhost:5173 in your browser.")
		argv := []string{npm, "run", "dev"}
		if c.P.Bool("open") {
			argv = append(argv, "--", "--open")
		}
		if code := runChild(argv, dir, c); code != 0 {
			return code
		}
	case "build":
		c.Println("Building desktop for production...")
		if code := runChild([]string{npm, "run", "build"}, dir, c); code != 0 {
			c.Errorf("Build failed.\n")
			return code
		}
		c.Printf("Desktop built to %s\n", filepath.Join(dir, "dist"))
	}
	return 0
}

// goos is replaceable in tests.
var goos = runtime.GOOS

func desktopLaunch(c *Ctx) int {
	if goos != "windows" {
		c.Println("remedy desktop launch is currently Windows-only.")
		c.Println("On macOS/Linux, open the installed Remedy Desktop app from your Applications menu or desktop entry.")
		c.Println("Installer downloads: " + releasesURL)
		return 1
	}
	uh, _ := os.UserHomeDir()
	var candidates []string
	if uh != "" {
		local := filepath.Join(uh, "AppData", "Local", "Programs")
		candidates = append(candidates,
			filepath.Join(local, "Remedy Desktop", "Remedy Desktop.exe"),
			filepath.Join(local, "remedy-desktop", "Remedy Desktop.exe"))
	}
	candidates = append(candidates, filepath.Join(`C:\Program Files`, "Remedy Desktop", "Remedy Desktop.exe"))
	for _, p := range candidates {
		if st, err := os.Stat(p); err == nil && !st.IsDir() {
			c.Printf("Launching: %s\n", p)
			// explorer.exe starts it from the shell, outside this process's
			// job, so the app keeps running after this command returns.
			if code := launchDetached(p, c); code != 0 {
				return code
			}
			return 0
		}
	}
	c.Println("Installed desktop app not found.")
	c.Println("Download the installer: " + releasesURL)
	return 1
}

// launchDetached is replaceable in tests.
var launchDetached = func(path string, c *Ctx) int {
	_, _, err := tools.RunAttached(context.Background(), []string{"explorer.exe", path}, "", nil, nil, discard{}, discard{}, 30*time.Second)
	if err != nil {
		c.Errorf("Launch failed: %v\n", err)
		return 1
	}
	return 0
}

// dialRuntime is replaceable in tests.
var dialRuntime = func() error {
	conn, err := net.DialTimeout("tcp", "127.0.0.1:7400", 2*time.Second)
	if err != nil {
		return err
	}
	return conn.Close()
}

func desktopStatus(c *Ctx) int {
	c.Println("Desktop Server Status")
	if err := dialRuntime(); err != nil {
		var opErr *net.OpError
		if errors.As(err, &opErr) {
			c.Println("  Server:    Offline (port 7400 not reachable)")
		} else {
			c.Printf("  Server:    Error — %v\n", err)
		}
	} else {
		c.Println("  Server:    Online  (127.0.0.1:7400)")
	}
	c.Println("Use 'remedy serve' to start remedy-runtime (local API authority).")
	return 0
}
