package connect

import (
	"bufio"
	"context"
	"crypto/rand"
	"crypto/subtle"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"net"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"sync"
	"time"
)

// iroh transport (spike).
//
// The remedy-iroh sidecar (native/iroh, Rust, iroh 1.x) owns an iroh endpoint
// that phones dial by public key. Each bidirectional stream it accepts is
// spliced to a loopback listener here, and from that point the bytes are an
// ordinary Connect pipe: RunSession does the Noise IK handshake against the
// pinned host key, the allowlist auth and the inner HTTP mux exactly as for
// LAN, relay and rendezvous. iroh adds QUIC hole punching and n0's public
// relays as the fallback; it never sees plaintext.

const (
	// IrohKeyName is the on-disk iroh endpoint secret (Ed25519), separate from
	// the Noise host key so either can rotate alone.
	IrohKeyName = "iroh.key"
	// IrohTokenLen is the loopback hop token length.
	IrohTokenLen = 32
	// irohHopTimeout bounds the token read on a loopback hop.
	irohHopTimeout = 5 * time.Second
	// IrohBinEnv overrides where the sidecar binary lives.
	IrohBinEnv = "REMEDY_IROH_BIN"
	// IrohEnableEnv turns the transport on without a settings change (spike).
	IrohEnableEnv = "REMEDY_CONNECT_IROH"
	// IrohRelayEnv sets a self-hosted iroh-relay URL without a settings change.
	IrohRelayEnv = "REMEDY_CONNECT_IROH_RELAY"
)

// ErrIroh is an iroh transport failure (sidecar, key or loopback hop).
var ErrIroh = errors.New("connect iroh")

var irohKeyMu sync.Mutex

// IrohKeyPath returns ~/.remedy/auth/connect/iroh.key.
func IrohKeyPath(home string) string {
	return filepath.Join(ResolveHome(home), "auth", "connect", IrohKeyName)
}

// LoadOrCreateIrohKey returns the 32-byte iroh endpoint secret, creating it
// once. It uses the host key's envelope (DPAPI on Windows, 0600 elsewhere).
func LoadOrCreateIrohKey(home string) ([]byte, error) {
	path := IrohKeyPath(home)
	irohKeyMu.Lock()
	defer irohKeyMu.Unlock()
	if st, err := os.Stat(path); err == nil && st.Size() > 0 {
		raw, err := os.ReadFile(path)
		if err != nil {
			return nil, err
		}
		return decodeHostKey(raw)
	}
	sk := make([]byte, 32)
	if _, err := rand.Read(sk); err != nil {
		return nil, err
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		return nil, err
	}
	raw, err := encodeHostKey(KeyPair{Private: sk})
	if err != nil {
		return nil, err
	}
	if err := writeBytesAtomic(path, raw); err != nil {
		return nil, err
	}
	return sk, nil
}

// IrohAdvert is what the pair QR advertises once the sidecar is online.
type IrohAdvert struct {
	EndpointID string
	RelayURL   string
}

var (
	irohAdvertMu sync.Mutex
	irohAdvert   IrohAdvert
)

// CurrentIrohAdvert returns the live endpoint id (empty when iroh is off).
func CurrentIrohAdvert() IrohAdvert {
	irohAdvertMu.Lock()
	defer irohAdvertMu.Unlock()
	return irohAdvert
}

func setIrohAdvert(a IrohAdvert) {
	irohAdvertMu.Lock()
	irohAdvert = a
	irohAdvertMu.Unlock()
}

// IrohEnabledFromEnv reports the spike's env switch.
func IrohEnabledFromEnv() bool {
	return asBool(strings.TrimSpace(os.Getenv(IrohEnableEnv)))
}

// IrohSidecarPath finds remedy-iroh: $REMEDY_IROH_BIN, else beside this
// executable (Tauri externalBin layout), else "" (transport stays off).
func IrohSidecarPath() string {
	if p := strings.TrimSpace(os.Getenv(IrohBinEnv)); p != "" {
		return p
	}
	name := "remedy-iroh"
	if runtime.GOOS == "windows" {
		name += ".exe"
	}
	exe, err := os.Executable()
	if err != nil {
		return ""
	}
	cand := filepath.Join(filepath.Dir(exe), name)
	if st, err := os.Stat(cand); err == nil && !st.IsDir() {
		return cand
	}
	return ""
}

// IrohSupervisorOpts configures RunIrohSupervisor.
type IrohSupervisorOpts struct {
	Home     string
	Bin      string // sidecar path; "" → IrohSidecarPath()
	RelayURL string // self-hosted iroh-relay; "" → n0 public relays
	// Session runs the Noise pipe for one spliced stream (supervisorSession).
	Session func(context.Context, net.Conn) error
	// OnEvent, when set, receives each sidecar event line (tests, metrics).
	OnEvent func(map[string]any)
}

// irohEvent is one stdout line from the sidecar.
type irohEvent struct {
	Event      string `json:"event"`
	EndpointID string `json:"endpoint_id"`
	RelayURL   string `json:"relay_url"`
	Remote     string `json:"remote"`
	Path       string `json:"path"`
	RTTMs      int64  `json:"rtt_ms"`
	Detail     string `json:"detail"`
	Reason     string `json:"reason"`
}

// RunIrohSupervisor keeps the sidecar running and feeds its loopback hops to
// opts.Session until ctx ends. It returns nil immediately when no sidecar
// binary is present, so a build without native/iroh behaves as before.
func RunIrohSupervisor(ctx context.Context, opts IrohSupervisorOpts) error {
	if opts.Session == nil {
		return fmt.Errorf("%w: supervisor needs a session runner", ErrIroh)
	}
	bin := opts.Bin
	if bin == "" {
		bin = IrohSidecarPath()
	}
	if bin == "" {
		log.Printf("connect: iroh requested but remedy-iroh is not installed beside the runtime; skipping")
		return nil
	}
	sk, err := LoadOrCreateIrohKey(opts.Home)
	if err != nil {
		return fmt.Errorf("%w: key: %v", ErrIroh, err)
	}
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		return err
	}
	defer func() { _ = ln.Close() }()
	token := make([]byte, IrohTokenLen)
	if _, err := rand.Read(token); err != nil {
		return err
	}

	var wg sync.WaitGroup
	defer wg.Wait()
	wg.Add(1)
	go func() {
		defer wg.Done()
		irohAcceptLoop(ctx, ln, token, opts.Session)
	}()
	go func() {
		<-ctx.Done()
		_ = ln.Close()
	}()

	backoff := time.Second
	defer setIrohAdvert(IrohAdvert{})
	for {
		started := time.Now()
		err := runIrohSidecar(ctx, bin, sk, token, ln.Addr().String(), opts)
		setIrohAdvert(IrohAdvert{})
		if ctx.Err() != nil {
			return ctx.Err()
		}
		if err != nil {
			log.Printf("connect: iroh sidecar exited: %v", err)
		}
		if time.Since(started) > time.Minute {
			backoff = time.Second
		}
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-time.After(backoff):
		}
		if backoff < 30*time.Second {
			backoff *= 2
		}
	}
}

func runIrohSidecar(ctx context.Context, bin string, sk, token []byte, forward string, opts IrohSupervisorOpts) error {
	proc, err := currentIrohStarter()(ctx, opts.Home, bin)
	if err != nil {
		return err
	}
	stopWatch := make(chan struct{})
	defer close(stopWatch)
	go func() {
		select {
		case <-ctx.Done():
			proc.Kill()
		case <-stopWatch:
		}
	}()
	cfg, _ := json.Marshal(map[string]string{
		"secret_key": hex.EncodeToString(sk),
		"forward":    forward,
		"token":      hex.EncodeToString(token),
		"relay_url":  strings.TrimSpace(opts.RelayURL),
	})
	if _, err := proc.Stdin.Write(append(cfg, '\n')); err != nil {
		proc.Kill()
		_ = proc.Wait()
		return err
	}
	// stdin stays open: closing it (or our exit) stops the sidecar.
	stdin, stdout := proc.Stdin, proc.Stdout
	sc := bufio.NewScanner(stdout)
	sc.Buffer(make([]byte, 0, 4096), 64*1024)
	for sc.Scan() {
		var ev irohEvent
		if json.Unmarshal(sc.Bytes(), &ev) != nil {
			continue
		}
		switch ev.Event {
		case "ready":
			setIrohAdvert(IrohAdvert{EndpointID: ev.EndpointID, RelayURL: strings.TrimSpace(opts.RelayURL)})
			log.Printf("connect: iroh ON — endpoint %s (home relay %s). Phones dial this id; "+
				"relays see IPs and timing, never content.", shortIrohID(ev.EndpointID), ev.RelayURL)
		case "conn", "path":
			log.Printf("connect: iroh %s %s path=%s rtt=%dms", ev.Event, ev.Remote, ev.Path, ev.RTTMs)
		case "error":
			log.Printf("connect: iroh error: %s", ev.Detail)
		}
		if opts.OnEvent != nil {
			var raw map[string]any
			if json.Unmarshal(sc.Bytes(), &raw) == nil {
				opts.OnEvent(raw)
			}
		}
	}
	_ = stdin.Close()
	return proc.Wait()
}

func shortIrohID(id string) string {
	if len(id) > 10 {
		return id[:10]
	}
	return id
}

// irohAcceptLoop admits loopback hops that present the sidecar's token.
func irohAcceptLoop(ctx context.Context, ln net.Listener, token []byte, session func(context.Context, net.Conn) error) {
	var wg sync.WaitGroup
	defer wg.Wait()
	for {
		conn, err := ln.Accept()
		if err != nil {
			return
		}
		wg.Add(1)
		go func(c net.Conn) {
			defer wg.Done()
			defer func() { _ = c.Close() }()
			if !irohHopAuthorized(c, token) {
				return
			}
			_ = session(ctx, c)
		}(conn)
	}
}

func irohHopAuthorized(c net.Conn, token []byte) bool {
	_ = c.SetReadDeadline(time.Now().Add(irohHopTimeout))
	got := make([]byte, len(token))
	if _, err := io.ReadFull(c, got); err != nil {
		return false
	}
	_ = c.SetReadDeadline(time.Time{})
	return subtle.ConstantTimeCompare(got, token) == 1
}
