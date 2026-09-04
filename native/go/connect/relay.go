package connect

import (
	"context"
	"encoding/binary"
	"errors"
	"fmt"
	"io"
	"net"
	"strings"
	"sync"
	"time"
)

const (
	// RelayMaxPayload is the max framed blob body (64 KiB). Matches Python MAX_PAYLOAD.
	RelayMaxPayload = 65536
	// RelayDefaultHost is the loopback bind used by the CLI when --host is omitted.
	RelayDefaultHost = "127.0.0.1"
	// RelayWaitPeer is how long a lone peer waits for its counterpart.
	RelayWaitPeer = 60 * time.Second
	// relayAcceptTimeout lets Stop interrupt Accept without spinning forever.
	relayAcceptTimeout = 200 * time.Millisecond
	// relayHelloTimeout bounds the initial 16-byte session-id read.
	relayHelloTimeout = 60 * time.Second
)

// ErrRelay is a Connect relay failure (bind policy, session id, or dial).
var ErrRelay = errors.New("connect relay")

// AssertRelayBind refuses wildcards and non-IPv4 hosts. Relays listen on a chosen IPv4 only.
func AssertRelayBind(host string) (string, error) {
	text, err := AssertChosenBind(host)
	if err != nil {
		return "", err
	}
	if !IsChosenIPv4(text) {
		return "", fmt.Errorf("%w: needs a chosen IPv4, not %q", ErrRelay, host)
	}
	return text, nil
}

type relaySlot struct {
	mu    sync.Mutex
	peers []net.Conn
	wake  chan struct{}
}

// Relay is an owner-run Grove Connect splice: two peers sharing a 16-byte
// session id exchange framed blobs (u32be length | rest). Bytes are copied,
// never decrypted, never logged.
type Relay struct {
	ln       net.Listener
	bindHost string
	bindPort int
	waitPeer time.Duration

	mu       sync.Mutex
	sessions map[string]*relaySlot
	stopCh   chan struct{}
	wg       sync.WaitGroup
}

// StartRelay binds host:port (port 0 = ephemeral) and serves in the background.
// waitPeer <= 0 uses RelayWaitPeer.
func StartRelay(host string, port int, waitPeer time.Duration) (*Relay, error) {
	chosen, err := AssertRelayBind(host)
	if err != nil {
		return nil, err
	}
	if port < 0 || port > 65535 {
		return nil, fmt.Errorf("%w: invalid port %d", ErrRelay, port)
	}
	if waitPeer <= 0 {
		waitPeer = RelayWaitPeer
	}

	addr := net.JoinHostPort(chosen, fmt.Sprintf("%d", port))
	ln, err := net.Listen("tcp", addr)
	if err != nil {
		return nil, err
	}
	tcpAddr, ok := ln.Addr().(*net.TCPAddr)
	if !ok || tcpAddr == nil {
		_ = ln.Close()
		return nil, fmt.Errorf("%w: listener has no TCP address", ErrRelay)
	}

	r := &Relay{
		ln:       ln,
		bindHost: tcpAddr.IP.String(),
		bindPort: tcpAddr.Port,
		waitPeer: waitPeer,
		sessions: make(map[string]*relaySlot),
		stopCh:   make(chan struct{}),
	}
	r.wg.Add(1)
	go r.acceptLoop()
	return r, nil
}

// Port returns the bound TCP port.
func (r *Relay) Port() int {
	return r.bindPort
}

// Addr returns host:port.
func (r *Relay) Addr() string {
	return net.JoinHostPort(r.bindHost, fmt.Sprintf("%d", r.bindPort))
}

// Stop closes the listener and waits for accept/handlers to finish. Idempotent.
func (r *Relay) Stop() {
	if r == nil {
		return
	}
	r.mu.Lock()
	select {
	case <-r.stopCh:
		r.mu.Unlock()
		return
	default:
		close(r.stopCh)
	}
	ln := r.ln
	r.mu.Unlock()
	if ln != nil {
		_ = ln.Close()
	}
	r.wg.Wait()
}

func (r *Relay) acceptLoop() {
	defer r.wg.Done()
	for {
		select {
		case <-r.stopCh:
			return
		default:
		}
		if tcp, ok := r.ln.(*net.TCPListener); ok {
			_ = tcp.SetDeadline(time.Now().Add(relayAcceptTimeout))
		}
		conn, err := r.ln.Accept()
		if err != nil {
			select {
			case <-r.stopCh:
				return
			default:
			}
			if ne, ok := err.(net.Error); ok && ne.Timeout() {
				continue
			}
			continue
		}
		r.wg.Add(1)
		go r.handleClient(conn)
	}
}

func (r *Relay) handleClient(conn net.Conn) {
	defer r.wg.Done()
	defer func() { _ = conn.Close() }()

	_ = conn.SetDeadline(time.Now().Add(relayHelloTimeout))
	sid := make([]byte, SessionIDLen)
	if _, err := io.ReadFull(conn, sid); err != nil {
		return
	}
	_ = conn.SetDeadline(time.Time{})

	key := string(sid)
	slot := r.slotFor(key)

	slot.mu.Lock()
	if len(slot.peers) >= 2 {
		slot.mu.Unlock()
		return
	}
	slot.peers = append(slot.peers, conn)
	var peer net.Conn
	if len(slot.peers) == 2 {
		peer = otherPeer(slot.peers, conn)
		select {
		case <-slot.wake:
		default:
			close(slot.wake)
		}
	}
	slot.mu.Unlock()

	if peer == nil {
		timer := time.NewTimer(r.waitPeer)
		defer timer.Stop()
		select {
		case <-slot.wake:
		case <-timer.C:
			r.dropPeer(key, slot, conn)
			return
		case <-r.stopCh:
			r.dropPeer(key, slot, conn)
			return
		}
		slot.mu.Lock()
		peer = otherPeer(slot.peers, conn)
		slot.mu.Unlock()
		if peer == nil {
			r.dropPeer(key, slot, conn)
			return
		}
	}

	copyFrames(conn, peer)
	r.dropPeer(key, slot, conn)
}

func (r *Relay) slotFor(key string) *relaySlot {
	r.mu.Lock()
	defer r.mu.Unlock()
	slot := r.sessions[key]
	if slot == nil {
		slot = &relaySlot{wake: make(chan struct{})}
		r.sessions[key] = slot
	}
	return slot
}

func (r *Relay) dropPeer(key string, slot *relaySlot, conn net.Conn) {
	slot.mu.Lock()
	for i, p := range slot.peers {
		if p == conn {
			slot.peers = append(slot.peers[:i], slot.peers[i+1:]...)
			break
		}
	}
	empty := len(slot.peers) == 0
	slot.mu.Unlock()
	if empty {
		r.mu.Lock()
		if r.sessions[key] == slot {
			delete(r.sessions, key)
		}
		r.mu.Unlock()
	}
}

func otherPeer(peers []net.Conn, self net.Conn) net.Conn {
	for _, p := range peers {
		if p != self {
			return p
		}
	}
	return nil
}

// copyFrames copies framed blobs src → dst. Does not inspect or log rest.
func copyFrames(src, dst net.Conn) {
	defer halfCloseWrite(dst)

	header := make([]byte, 4)
	for {
		if _, err := io.ReadFull(src, header); err != nil {
			break
		}
		length := binary.BigEndian.Uint32(header)
		if length > RelayMaxPayload {
			break
		}
		var payload []byte
		if length > 0 {
			payload = make([]byte, length)
			if _, err := io.ReadFull(src, payload); err != nil {
				break
			}
		}
		if _, err := dst.Write(header); err != nil {
			break
		}
		if length > 0 {
			if _, err := dst.Write(payload); err != nil {
				break
			}
		}
	}
}

func halfCloseWrite(c net.Conn) {
	type closeWriter interface{ CloseWrite() error }
	if cw, ok := c.(closeWriter); ok {
		_ = cw.CloseWrite()
	}
}

// DialRelay connects to an owner-run relay URL and writes the 16-byte session id.
func DialRelay(ctx context.Context, rawURL string, sessionID []byte) (net.Conn, error) {
	if len(sessionID) != SessionIDLen {
		return nil, fmt.Errorf("%w: session id must be %d bytes", ErrRelay, SessionIDLen)
	}
	host, port, err := ParseRelayEndpoint(rawURL)
	if err != nil {
		return nil, err
	}
	d := net.Dialer{}
	conn, err := d.DialContext(ctx, "tcp", net.JoinHostPort(host, fmt.Sprintf("%d", port)))
	if err != nil {
		return nil, err
	}
	if deadline, ok := ctx.Deadline(); ok {
		_ = conn.SetDeadline(deadline)
	}
	if _, err := conn.Write(sessionID); err != nil {
		_ = conn.Close()
		return nil, err
	}
	_ = conn.SetDeadline(time.Time{})
	return conn, nil
}

// RelayConfigured returns the trimmed connect_relay_url when present and valid,
// or "" when unset. Fail-closed on a malformed URL.
func RelayConfigured(raw string) (string, error) {
	text := strings.TrimSpace(raw)
	if text == "" {
		return "", nil
	}
	if _, _, err := ParseRelayEndpoint(text); err != nil {
		return "", err
	}
	return text, nil
}

// RelayConnHandler is invoked once a relay dial completes for a session id.
// The handler owns the connection for the session lifetime; returning closes it.
type RelayConnHandler func(ctx context.Context, conn net.Conn)

// RelaySupervisorOpts configures the outbound relay dialer supervisor.
type RelaySupervisorOpts struct {
	URL      string
	Home     string
	Handler  RelayConnHandler
	Interval time.Duration // how often to refresh wanted SIDs; default 1s
	DialWait time.Duration // per-dial timeout; default 20s
}

// RunRelaySupervisor keeps one outbound relay waiter per active rendezvous sid
// (pair window + paired devices). Matches Python _relay_supervisor: reconnect
// with backoff, skip while paused, retire SIDs that leave the wanted set.
func RunRelaySupervisor(ctx context.Context, opts RelaySupervisorOpts) error {
	url, err := RelayConfigured(opts.URL)
	if err != nil {
		return err
	}
	if url == "" {
		return nil
	}
	if opts.Handler == nil {
		return fmt.Errorf("%w: relay supervisor needs a handler", ErrRelay)
	}
	interval := opts.Interval
	if interval <= 0 {
		interval = time.Second
	}
	dialWait := opts.DialWait
	if dialWait <= 0 {
		dialWait = 20 * time.Second
	}

	type liveDial struct {
		cancel context.CancelFunc
		done   <-chan struct{}
	}
	live := map[string]liveDial{}
	defer func() {
		for _, d := range live {
			d.cancel()
		}
		for _, d := range live {
			<-d.done
		}
	}()

	tick := time.NewTicker(interval)
	defer tick.Stop()
	for {
		wanted, err := RendezvousSIDs(opts.Home)
		if err != nil {
			return err
		}
		wantKeys := map[string][]byte{}
		for _, sid := range wanted {
			if len(sid) == SessionIDLen {
				wantKeys[string(sid)] = sid
			}
		}
		for key, d := range live {
			if _, ok := wantKeys[key]; !ok {
				d.cancel()
				<-d.done
				delete(live, key)
			}
		}
		for key, sid := range wantKeys {
			if _, ok := live[key]; ok {
				continue
			}
			sidCopy := append([]byte(nil), sid...)
			dctx, cancel := context.WithCancel(ctx)
			done := make(chan struct{})
			live[key] = liveDial{cancel: cancel, done: done}
			go func() {
				defer close(done)
				runRelayDialLoop(dctx, url, opts.Home, sidCopy, dialWait, opts.Handler)
			}()
		}

		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-tick.C:
		}
	}
}

func runRelayDialLoop(ctx context.Context, url, home string, sid []byte, dialWait time.Duration, handler RelayConnHandler) {
	backoff := time.Second
	for {
		if IsPaused(home) {
			select {
			case <-ctx.Done():
				return
			case <-time.After(500 * time.Millisecond):
				continue
			}
		}
		dctx, cancel := context.WithTimeout(ctx, dialWait)
		conn, err := DialRelay(dctx, url, sid)
		cancel()
		if err != nil {
			select {
			case <-ctx.Done():
				return
			case <-time.After(backoff):
			}
			backoff = backoff + backoff/2
			if backoff > 15*time.Second {
				backoff = 15 * time.Second
			}
			continue
		}
		backoff = time.Second
		handler(ctx, conn)
		_ = conn.Close()
		select {
		case <-ctx.Done():
			return
		default:
		}
	}
}
