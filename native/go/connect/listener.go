package connect

import (
	"context"
	"errors"
	"fmt"
	"net"
	"strings"
	"sync"
	"time"
)

const (
	// HandshakeRate is max new accepts per peer IP inside HandshakeWindow.
	HandshakeRate = 10
	// HandshakeWindow is the sliding window for HandshakeRate.
	HandshakeWindow = 60 * time.Second
	// DefaultBindPort is used when Settings omit / mis-set connect_bind_port.
	DefaultBindPort = 7401
)

// ConnHandler is invoked for each accepted TCP connection.
// The listener registers the conn before the call and unregisters after return.
type ConnHandler func(ctx context.Context, conn net.Conn)

// Listener is a chosen-IPv4 TCP Connect gateway (never 0.0.0.0 / ::).
type Listener struct {
	mu       sync.Mutex
	ln       net.Listener
	bindHost string
	bindPort int
	conns    map[net.Conn]string
	rate     map[string][]time.Time
	cancel   context.CancelFunc
	wg       sync.WaitGroup
	handler  ConnHandler
}

// NewListener returns an idle Connect listener.
func NewListener() *Listener {
	return &Listener{
		conns: make(map[net.Conn]string),
		rate:  make(map[string][]time.Time),
	}
}

// ListeningAddr returns the bound host and port, or ok=false when not listening.
func (l *Listener) ListeningAddr() (host string, port int, ok bool) {
	l.mu.Lock()
	defer l.mu.Unlock()
	if l.ln == nil || l.bindHost == "" {
		return "", 0, false
	}
	return l.bindHost, l.bindPort, true
}

// Start binds host:port (port 0 = ephemeral) and accepts connections.
func (l *Listener) Start(host string, port int, handler ConnHandler) error {
	host, err := AssertChosenBind(host)
	if err != nil {
		return err
	}
	if !IsChosenIPv4(host) {
		return fmt.Errorf("%w: connect bind must be a chosen IPv4, not wildcard", ErrBind)
	}
	if port < 0 || port > 65535 {
		return fmt.Errorf("%w: invalid port", ErrBind)
	}

	l.mu.Lock()
	defer l.mu.Unlock()
	if l.ln != nil {
		return fmt.Errorf("%w: already listening", ErrBind)
	}

	addr := net.JoinHostPort(host, fmt.Sprintf("%d", port))
	ln, err := net.Listen("tcp", addr)
	if err != nil {
		return err
	}
	tcpAddr, ok := ln.Addr().(*net.TCPAddr)
	if !ok || tcpAddr == nil {
		_ = ln.Close()
		return fmt.Errorf("%w: listener has no TCP address", ErrBind)
	}

	ctx, cancel := context.WithCancel(context.Background())
	l.ln = ln
	l.bindHost = tcpAddr.IP.String()
	l.bindPort = tcpAddr.Port
	l.cancel = cancel
	l.handler = handler
	l.wg.Add(1)
	go l.acceptLoop(ctx)
	return nil
}

// Stop closes the listener and every live session socket.
func (l *Listener) Stop() error {
	l.mu.Lock()
	cancel := l.cancel
	ln := l.ln
	l.ln = nil
	l.bindHost = ""
	l.bindPort = 0
	l.cancel = nil
	l.mu.Unlock()

	if cancel != nil {
		cancel()
	}
	var closeErr error
	if ln != nil {
		closeErr = ln.Close()
	}
	l.DropAllSessions()
	l.wg.Wait()
	return closeErr
}

func (l *Listener) acceptLoop(ctx context.Context) {
	defer l.wg.Done()
	for {
		l.mu.Lock()
		ln := l.ln
		l.mu.Unlock()
		if ln == nil {
			return
		}
		conn, err := ln.Accept()
		if err != nil {
			select {
			case <-ctx.Done():
				return
			default:
				if errors.Is(err, net.ErrClosed) {
					return
				}
				var ne net.Error
				if errors.As(err, &ne) && ne.Timeout() {
					time.Sleep(5 * time.Millisecond)
					continue
				}
				return
			}
		}
		if !l.rateAllow(peerIP(conn)) {
			_ = conn.Close()
			continue
		}
		l.Register(conn, "")
		l.wg.Add(1)
		go func(c net.Conn) {
			defer l.wg.Done()
			defer l.Unregister(c)
			defer func() { _ = c.Close() }()
			l.mu.Lock()
			h := l.handler
			l.mu.Unlock()
			if h != nil {
				h(ctx, c)
			}
		}(conn)
	}
}

// Register tracks a live Connect socket (device id may be empty).
func (l *Listener) Register(conn net.Conn, deviceID string) {
	if conn == nil {
		return
	}
	l.mu.Lock()
	defer l.mu.Unlock()
	l.conns[conn] = deviceID
}

// BindDevice associates a registered connection with a paired device id.
func (l *Listener) BindDevice(conn net.Conn, deviceID string) {
	if conn == nil {
		return
	}
	l.mu.Lock()
	defer l.mu.Unlock()
	if _, ok := l.conns[conn]; ok {
		l.conns[conn] = deviceID
	}
}

// Unregister drops tracking for conn without closing it.
func (l *Listener) Unregister(conn net.Conn) {
	if conn == nil {
		return
	}
	l.mu.Lock()
	defer l.mu.Unlock()
	delete(l.conns, conn)
}

// DeviceID returns the bound device id for conn, or empty.
func (l *Listener) DeviceID(conn net.Conn) string {
	l.mu.Lock()
	defer l.mu.Unlock()
	return l.conns[conn]
}

// LiveCount returns the number of tracked sockets.
func (l *Listener) LiveCount() int {
	l.mu.Lock()
	defer l.mu.Unlock()
	return len(l.conns)
}

// DropAllSessions closes every live Connect socket (pause).
func (l *Listener) DropAllSessions() {
	l.mu.Lock()
	items := make([]net.Conn, 0, len(l.conns))
	for c := range l.conns {
		items = append(items, c)
	}
	l.conns = make(map[net.Conn]string)
	l.mu.Unlock()
	for _, c := range items {
		_ = c.Close()
	}
}

// DropSessionsForDevice closes sockets bound to deviceID.
// When no connection has a device id, all sockets drop (fail closed).
// Empty deviceID drops every live socket.
func (l *Listener) DropSessionsForDevice(deviceID string) {
	want := deviceID
	if want == "" {
		l.DropAllSessions()
		return
	}
	l.mu.Lock()
	mapped := false
	for _, did := range l.conns {
		if did != "" {
			mapped = true
			break
		}
	}
	var drop []net.Conn
	if !mapped {
		for c := range l.conns {
			drop = append(drop, c)
		}
		l.conns = make(map[net.Conn]string)
	} else {
		for c, did := range l.conns {
			if did == "" || did == want {
				drop = append(drop, c)
				delete(l.conns, c)
			}
		}
	}
	l.mu.Unlock()
	for _, c := range drop {
		_ = c.Close()
	}
}

func (l *Listener) rateAllow(ip string) bool {
	now := time.Now()
	l.mu.Lock()
	defer l.mu.Unlock()
	bucket := l.rate[ip]
	kept := bucket[:0]
	for _, ts := range bucket {
		if now.Sub(ts) <= HandshakeWindow {
			kept = append(kept, ts)
		}
	}
	if len(kept) >= HandshakeRate {
		l.rate[ip] = kept
		return false
	}
	l.rate[ip] = append(kept, now)
	return true
}

func peerIP(conn net.Conn) string {
	if conn == nil {
		return "?"
	}
	ra := conn.RemoteAddr()
	if ra == nil {
		return "?"
	}
	host, _, err := net.SplitHostPort(ra.String())
	if err != nil {
		return ra.String()
	}
	return host
}

// GatewayConfig is the owner Settings slice that gates the Connect listener.
type GatewayConfig struct {
	Enabled bool
	Host    string
	Port    int
}

// EnabledChosen returns whether Connect should listen, plus the chosen host/port.
func EnabledChosen(cfg GatewayConfig) (ok bool, host string, port int) {
	port = cfg.Port
	if port < 0 || port > 65535 {
		port = DefaultBindPort
	}
	if !cfg.Enabled {
		return false, "", port
	}
	host = strings.TrimSpace(cfg.Host)
	if host == "" {
		return false, "", port
	}
	if IsWildcardBind(host) || !IsChosenIPv4(host) {
		return false, host, port
	}
	if _, err := AssertChosenBind(host); err != nil {
		return false, host, port
	}
	return true, host, port
}
