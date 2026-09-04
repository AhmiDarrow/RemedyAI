package connect_test

import (
	"context"
	"errors"
	"net"
	"strconv"
	"sync"
	"testing"
	"time"

	"github.com/AhmiDarrow/RemedyAI/native/go/connect"
)

type sinkConn struct {
	net.Conn
	closed bool
	mu     sync.Mutex
}

func (s *sinkConn) Close() error {
	s.mu.Lock()
	s.closed = true
	s.mu.Unlock()
	if s.Conn != nil {
		return s.Conn.Close()
	}
	return nil
}

func (s *sinkConn) isClosed() bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.closed
}

func newSinkPair(t *testing.T) (a, b *sinkConn) {
	t.Helper()
	c1, c2 := net.Pipe()
	return &sinkConn{Conn: c1}, &sinkConn{Conn: c2}
}

func TestListenerWildcardStartRefused(t *testing.T) {
	l := connect.NewListener()
	for _, host := range []string{"0.0.0.0", "*", "::"} {
		if err := l.Start(host, 0, nil); !errors.Is(err, connect.ErrBind) {
			t.Fatalf("%s: %v", host, err)
		}
		if _, _, ok := l.ListeningAddr(); ok {
			t.Fatal("must not listen after refuse")
		}
	}
}

func TestListenerStartStopLoopback(t *testing.T) {
	l := connect.NewListener()
	defer func() { _ = l.Stop() }()

	hold := make(chan struct{})
	err := l.Start("127.0.0.1", 0, func(ctx context.Context, conn net.Conn) {
		select {
		case <-ctx.Done():
		case <-hold:
		}
	})
	if err != nil {
		t.Fatal(err)
	}
	host, port, ok := l.ListeningAddr()
	if !ok || host != "127.0.0.1" || port == 0 {
		t.Fatalf("addr=%s:%d ok=%v", host, port, ok)
	}

	conn, err := net.DialTimeout("tcp", net.JoinHostPort(host, strconv.Itoa(port)), time.Second)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()

	deadline := time.Now().Add(2 * time.Second)
	for l.LiveCount() < 1 && time.Now().Before(deadline) {
		time.Sleep(5 * time.Millisecond)
	}
	if l.LiveCount() < 1 {
		t.Fatal("accepted conn not registered")
	}

	_ = l.Stop()
	if _, _, ok := l.ListeningAddr(); ok {
		t.Fatal("still listening after stop")
	}
	close(hold)
}

func TestListenerDoesNotTouchSidecarPort(t *testing.T) {
	l := connect.NewListener()
	defer func() { _ = l.Stop() }()
	if err := l.Start("127.0.0.1", 0, nil); err != nil {
		t.Fatal(err)
	}
	_, port, ok := l.ListeningAddr()
	if !ok || port == 7400 {
		t.Fatalf("unexpected port %d", port)
	}
}

func TestDropSessionsForDeviceKeepsOther(t *testing.T) {
	l := connect.NewListener()
	a, aPeer := newSinkPair(t)
	b, bPeer := newSinkPair(t)
	defer aPeer.Close()
	defer bPeer.Close()

	l.Register(a, "")
	l.BindDevice(a, "aaaaaaaaaaaaaaaa")
	l.Register(b, "")
	l.BindDevice(b, "bbbbbbbbbbbbbbbb")

	l.DropSessionsForDevice("aaaaaaaaaaaaaaaa")
	if !a.isClosed() {
		t.Fatal("device a not closed")
	}
	if b.isClosed() {
		t.Fatal("device b closed")
	}
	_ = b.Close()
}

func TestDropUnmappedWritersFailClosed(t *testing.T) {
	l := connect.NewListener()
	a, aPeer := newSinkPair(t)
	b, bPeer := newSinkPair(t)
	defer aPeer.Close()
	defer bPeer.Close()
	l.Register(a, "")
	l.Register(b, "")
	l.DropSessionsForDevice("aaaaaaaaaaaaaaaa")
	if !a.isClosed() || !b.isClosed() {
		t.Fatal("unmapped revoke must drop all")
	}
}

func TestDropEmptyDeviceIDFailsClosed(t *testing.T) {
	l := connect.NewListener()
	a, aPeer := newSinkPair(t)
	b, bPeer := newSinkPair(t)
	defer aPeer.Close()
	defer bPeer.Close()
	l.Register(a, "")
	l.BindDevice(a, "aaaaaaaaaaaaaaaa")
	l.Register(b, "")
	l.BindDevice(b, "bbbbbbbbbbbbbbbb")
	l.DropSessionsForDevice("")
	if !a.isClosed() || !b.isClosed() {
		t.Fatal("empty revoke must drop all")
	}
}

func TestDropKeepsOtherWhenOneUnmapped(t *testing.T) {
	l := connect.NewListener()
	a, aPeer := newSinkPair(t)
	b, bPeer := newSinkPair(t)
	u, uPeer := newSinkPair(t)
	defer aPeer.Close()
	defer bPeer.Close()
	defer uPeer.Close()
	l.Register(a, "")
	l.BindDevice(a, "aaaaaaaaaaaaaaaa")
	l.Register(b, "")
	l.BindDevice(b, "bbbbbbbbbbbbbbbb")
	l.Register(u, "")
	l.DropSessionsForDevice("aaaaaaaaaaaaaaaa")
	if !a.isClosed() || !u.isClosed() {
		t.Fatal("target + unmapped must close")
	}
	if b.isClosed() {
		t.Fatal("other device must stay")
	}
	_ = b.Close()
}

func TestDropAllSessions(t *testing.T) {
	l := connect.NewListener()
	a, aPeer := newSinkPair(t)
	b, bPeer := newSinkPair(t)
	defer aPeer.Close()
	defer bPeer.Close()
	l.Register(a, "")
	l.BindDevice(a, "aaaaaaaaaaaaaaaa")
	l.Register(b, "")
	l.BindDevice(b, "bbbbbbbbbbbbbbbb")
	l.DropAllSessions()
	if !a.isClosed() || !b.isClosed() {
		t.Fatal("pause must drop every socket")
	}
	if l.LiveCount() != 0 {
		t.Fatalf("live=%d", l.LiveCount())
	}
}

func TestHandshakeRateLimit(t *testing.T) {
	l := connect.NewListener()
	defer func() { _ = l.Stop() }()

	var mu sync.Mutex
	accepted := 0
	err := l.Start("127.0.0.1", 0, func(ctx context.Context, conn net.Conn) {
		mu.Lock()
		accepted++
		mu.Unlock()
		<-ctx.Done()
	})
	if err != nil {
		t.Fatal(err)
	}
	host, port, ok := l.ListeningAddr()
	if !ok {
		t.Fatal("not listening")
	}
	addr := net.JoinHostPort(host, strconv.Itoa(port))

	var dialed int
	for i := 0; i < connect.HandshakeRate+5; i++ {
		c, err := net.DialTimeout("tcp", addr, time.Second)
		if err != nil {
			continue
		}
		dialed++
		_ = c.SetDeadline(time.Now().Add(50 * time.Millisecond))
		buf := make([]byte, 1)
		_, _ = c.Read(buf)
		_ = c.Close()
	}
	deadline := time.Now().Add(2 * time.Second)
	for time.Now().Before(deadline) {
		mu.Lock()
		n := accepted
		mu.Unlock()
		if n >= connect.HandshakeRate {
			break
		}
		time.Sleep(5 * time.Millisecond)
	}
	mu.Lock()
	n := accepted
	mu.Unlock()
	if n != connect.HandshakeRate {
		t.Fatalf("accepted %d want %d (dialed %d)", n, connect.HandshakeRate, dialed)
	}
}

func TestReadRecordOverTCP(t *testing.T) {
	l := connect.NewListener()
	defer func() { _ = l.Stop() }()

	got := make(chan []byte, 1)
	err := l.Start("127.0.0.1", 0, func(ctx context.Context, conn net.Conn) {
		nonce, ct, err := connect.ReadRecord(conn)
		if err != nil {
			got <- nil
			return
		}
		blob, err := connect.PackRecord(nonce, ct)
		if err != nil {
			got <- nil
			return
		}
		got <- blob
	})
	if err != nil {
		t.Fatal(err)
	}
	host, port, ok := l.ListeningAddr()
	if !ok {
		t.Fatal("not listening")
	}
	fx := loadRecordFixture(t)
	want := mustHex(t, fx.PackVectors[3].RecordHex)
	c, err := net.DialTimeout("tcp", net.JoinHostPort(host, strconv.Itoa(port)), time.Second)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := c.Write(want); err != nil {
		t.Fatal(err)
	}
	select {
	case blob := <-got:
		if blob == nil {
			t.Fatal("handler failed")
		}
		assertHex(t, "tcp record", blob, fx.PackVectors[3].RecordHex)
	case <-time.After(2 * time.Second):
		t.Fatal("timeout")
	}
	_ = c.Close()
}

func TestMaybeStartViaEnabledChosen(t *testing.T) {
	ok, _, _ := connect.EnabledChosen(connect.GatewayConfig{})
	if ok {
		t.Fatal("default must be off")
	}
	l := connect.NewListener()
	defer func() { _ = l.Stop() }()
	if _, _, listening := l.ListeningAddr(); listening {
		t.Fatal("must stay idle when disabled")
	}

	ok, host, port := connect.EnabledChosen(connect.GatewayConfig{
		Enabled: true,
		Host:    "127.0.0.1",
		Port:    0,
	})
	if !ok {
		t.Fatal("expected enable")
	}
	if err := l.Start(host, port, nil); err != nil {
		t.Fatal(err)
	}
	if _, _, listening := l.ListeningAddr(); !listening {
		t.Fatal("expected listening")
	}
}
