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
	"net/netip"
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
	// Addrs are the direct ip:port candidates for the QR (router-mapped
	// public address first, then global IPv6, then LAN).
	Addrs     []string
	RelayURL  string
	RelayMode string // "off" (default), "custom", or "n0"
	// PunchBrokers is the irp= value when hole punching is on ("" = off).
	PunchBrokers string
}

// maxIrohQRAddrs bounds the ira= list.
const maxIrohQRAddrs = 6

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
	Home string
	Bin  string // sidecar path; "" → IrohSidecarPath()
	// Port is the fixed UDP port (0 → IrohDefaultPort; -1 → ephemeral, tests).
	Port int
	// RelayMode: "off" (default: the PC is its own endpoint, no third party),
	// "custom" (RelayURL, the owner's iroh-relay) or "n0" (public, advanced).
	RelayMode string
	RelayURL  string
	// NoPortmap skips UPnP/PCP/NAT-PMP on the home router.
	NoPortmap bool
	// STUNServers for the self-test's public-address check (nil → defaults;
	// empty non-nil → skip STUN).
	STUNServers []string
	// Punch turns on relay-free hole punching through CGNAT: the PC answers
	// encrypted address offers on public MQTT brokers (PunchBrokers, nil →
	// the public rendezvous brokers). Off by default.
	Punch        bool
	PunchBrokers []RDVEndpoint
	// Session runs the Noise pipe for one spliced stream (supervisorSession).
	Session func(context.Context, net.Conn) error
	// OnEvent, when set, receives each sidecar event line (tests, metrics).
	OnEvent func(map[string]any)
}

// irohEvent is one stdout line from the sidecar.
type irohEvent struct {
	Event      string         `json:"event"`
	EndpointID string         `json:"endpoint_id"`
	RelayURL   string         `json:"relay_url"`
	Remote     string         `json:"remote"`
	Path       string         `json:"path"`
	RTTMs      int64          `json:"rtt_ms"`
	Detail     string         `json:"detail"`
	Reason     string         `json:"reason"`
	RelayMode  string         `json:"relay_mode"`
	Addrs      []string       `json:"addrs"`
	External   *string        `json:"external"`
	Probe      map[string]any `json:"probe"`
	ID         string         `json:"id"`
	PunchErr   string         `json:"error"`
	Port       int            `json:"port"`
}

// Live punch hook: set while a sidecar runs, nil otherwise.
var (
	irohPunchMu sync.Mutex
	irohPunch   PunchFunc
)

func setIrohPunch(f PunchFunc) {
	irohPunchMu.Lock()
	irohPunch = f
	irohPunchMu.Unlock()
}

// currentIrohPunch asks whichever sidecar is running to open a punch port.
func currentIrohPunch(ctx context.Context, peerID string, peerAddrs []string) ([]string, error) {
	irohPunchMu.Lock()
	f := irohPunch
	irohPunchMu.Unlock()
	if f == nil {
		return nil, fmt.Errorf("%w: sidecar not running", ErrIroh)
	}
	return f(ctx, peerID, peerAddrs)
}

// irohRelayMode normalises the configured relay policy.
func irohRelayMode(mode, url string) string {
	switch strings.ToLower(strings.TrimSpace(mode)) {
	case "n0":
		return "n0"
	case "custom":
		return "custom"
	case "off":
		return "off"
	}
	if strings.TrimSpace(url) != "" {
		return "custom"
	}
	return "off"
}

// irohLog is swappable so tests stay quiet.
var logIroh = func(line string) { log.Print(line) }

// orderIrohAddrs keeps public candidates first and drops loopback/link-local.
func orderIrohAddrs(addrs []string, mapped string) []string {
	var public, v6, lan []string
	seen := map[string]bool{}
	add := func(dst *[]string, a string) {
		if !seen[a] {
			seen[a] = true
			*dst = append(*dst, a)
		}
	}
	if mapped != "" {
		add(&public, mapped)
	}
	for _, a := range addrs {
		ap, err := netip.ParseAddrPort(a)
		if err != nil {
			continue
		}
		ip := ap.Addr().Unmap()
		switch {
		case ip.IsLoopback() || ip.IsLinkLocalUnicast() || ip.IsUnspecified():
		case isPublicAddr(ip) && ip.Is6():
			add(&v6, a)
		case isPublicAddr(ip):
			add(&public, a)
		default:
			add(&lan, a)
		}
	}
	out := append(append(public, v6...), lan...)
	if len(out) > maxIrohQRAddrs {
		out = out[:maxIrohQRAddrs]
	}
	return out
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

	if opts.Punch {
		hostKP, err := LoadOrCreateHostKeyPair(opts.Home)
		if err != nil {
			return fmt.Errorf("%w: host key: %v", ErrIroh, err)
		}
		brokers := opts.PunchBrokers
		if len(brokers) == 0 {
			brokers = DefaultPunchBrokers()
		}
		wg.Add(1)
		go func() {
			defer wg.Done()
			RunIrohPunchResponder(ctx, hostKP.Public, brokers, currentIrohPunch)
		}()
	}

	backoff := time.Second
	defer setIrohAdvert(IrohAdvert{})
	defer updateIrohStatus(func(s *IrohStatus) {
		*s = IrohStatus{Verdict: ReachOff, Message: "Reach from mobile data is off."}
	})
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
	mode := irohRelayMode(opts.RelayMode, opts.RelayURL)
	port := opts.Port
	if port == 0 {
		port = IrohDefaultPort
	}
	if port < 0 {
		port = 0
	}
	sidecarCfg := map[string]any{
		"secret_key": hex.EncodeToString(sk),
		"forward":    forward,
		"token":      hex.EncodeToString(token),
		"relay_mode": mode,
		"bind_port":  port,
		"portmap":    !opts.NoPortmap,
	}
	if mode == "custom" {
		sidecarCfg["relay_urls"] = []string{strings.TrimSpace(opts.RelayURL)}
	}
	cfg, _ := json.Marshal(sidecarCfg)
	updateIrohStatus(func(s *IrohStatus) {
		*s = IrohStatus{Enabled: true, Port: port, RelayMode: mode, Punch: opts.Punch, Verdict: ReachChecking,
			Message: "Checking whether your phone can reach this PC from mobile data…"}
	})
	var (
		advMu   sync.Mutex
		adv     = IrohAdvert{RelayMode: mode}
		rawAddr []string
		mapped  string
		testGen int
	)
	if mode == "custom" {
		adv.RelayURL = strings.TrimSpace(opts.RelayURL)
	}
	if opts.Punch {
		brokers := opts.PunchBrokers
		if len(brokers) == 0 {
			brokers = DefaultPunchBrokers()
		}
		adv.PunchBrokers = PunchBrokerList(brokers)
	}
	publish := func() {
		adv.Addrs = orderIrohAddrs(rawAddr, mapped)
		setIrohAdvert(adv)
		updateIrohStatus(func(s *IrohStatus) {
			s.EndpointID = adv.EndpointID
			s.Addrs = append([]string(nil), rawAddr...)
			s.Mapped = mapped
		})
	}
	// Debounced self-test: addresses and the router mapping arrive over a
	// few seconds; test once they settle.
	scheduleTest := func() {
		testGen++
		gen := testGen
		time.AfterFunc(3*time.Second, func() {
			advMu.Lock()
			current := gen == testGen
			advMu.Unlock()
			if current && ctx.Err() == nil {
				runIrohSelfTest(ctx, opts.STUNServers)
			}
		})
	}
	if _, err := proc.Stdin.Write(append(cfg, '\n')); err != nil {
		proc.Kill()
		_ = proc.Wait()
		return err
	}
	// stdin stays open: closing it (or our exit) stops the sidecar. Later
	// lines are commands; punch answers come back as punch_ready events.
	stdin, stdout := proc.Stdin, proc.Stdout
	var (
		cmdMu   sync.Mutex
		waiters = map[string]chan irohEvent{}
	)
	setIrohPunch(func(pctx context.Context, peerID string, peerAddrs []string) ([]string, error) {
		idb := make([]byte, 8)
		if _, err := rand.Read(idb); err != nil {
			return nil, err
		}
		id := hex.EncodeToString(idb)
		ch := make(chan irohEvent, 1)
		stunList := opts.STUNServers
		if stunList == nil {
			stunList = []string{}
		}
		line, _ := json.Marshal(map[string]any{"cmd": "punch", "id": id, "peer_id": peerID,
			"peer_addrs": peerAddrs, "stun": stunList})
		cmdMu.Lock()
		waiters[id] = ch
		_, werr := stdin.Write(append(line, '\n'))
		cmdMu.Unlock()
		defer func() {
			cmdMu.Lock()
			delete(waiters, id)
			cmdMu.Unlock()
		}()
		if werr != nil {
			return nil, werr
		}
		select {
		case ev := <-ch:
			if ev.PunchErr != "" {
				return nil, fmt.Errorf("%w: punch: %s", ErrIroh, ev.PunchErr)
			}
			logIroh(fmt.Sprintf("connect: hole punch port %d ready for %s", ev.Port, shortIrohID(peerID)))
			return ev.Addrs, nil
		case <-pctx.Done():
			return nil, pctx.Err()
		}
	})
	defer setIrohPunch(nil)
	sc := bufio.NewScanner(stdout)
	sc.Buffer(make([]byte, 0, 4096), 64*1024)
	for sc.Scan() {
		var ev irohEvent
		if json.Unmarshal(sc.Bytes(), &ev) != nil {
			continue
		}
		if ev.Event == "punch_ready" {
			cmdMu.Lock()
			ch := waiters[ev.ID]
			cmdMu.Unlock()
			if ch != nil {
				select {
				case ch <- ev:
				default:
				}
			}
			continue
		}
		advMu.Lock()
		switch ev.Event {
		case "ready":
			adv.EndpointID = ev.EndpointID
			publish()
			scheduleTest()
			logIroh(fmt.Sprintf("connect: mobile-data endpoint ON — iroh id %s, UDP %d, relay %s. "+
				"Phones dial this PC directly; any relay sees IPs and timing, never content.",
				shortIrohID(ev.EndpointID), port, mode))
		case "addrs":
			rawAddr = append([]string(nil), ev.Addrs...)
			publish()
			scheduleTest()
		case "mapped":
			mapped = ""
			if ev.External != nil {
				mapped = *ev.External
			}
			publish()
			scheduleTest()
		case "portmap_probe":
			var protos []string
			for _, k := range []string{"upnp", "pcp", "nat_pmp"} {
				if v, _ := ev.Probe[k].(bool); v {
					protos = append(protos, k)
				}
			}
			updateIrohStatus(func(s *IrohStatus) { s.MapProtos = protos })
		case "conn", "path":
			log.Printf("connect: iroh %s %s path=%s rtt=%dms", ev.Event, ev.Remote, ev.Path, ev.RTTMs)
		case "error":
			log.Printf("connect: iroh error: %s", ev.Detail)
		}
		advMu.Unlock()
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
