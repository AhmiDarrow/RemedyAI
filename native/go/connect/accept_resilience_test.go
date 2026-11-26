package connect

import (
	"context"
	"errors"
	"net"
	"sync/atomic"
	"testing"
	"time"
)

type flakyListener struct {
	net.Listener
	failures atomic.Int32
}

func (f *flakyListener) Accept() (net.Conn, error) {
	if f.failures.Add(-1) >= 0 {
		return nil, errors.New("accept: too many open files")
	}
	return f.Listener.Accept()
}

// A temporary accept error must not stop the phone listener for good.
func TestAcceptLoopSurvivesTemporaryErrors(t *testing.T) {
	inner, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	fl := &flakyListener{Listener: inner}
	fl.failures.Store(3)
	got := make(chan struct{}, 1)
	l := NewListener()
	l.ln = fl
	l.handler = func(context.Context, net.Conn) { got <- struct{}{} }
	ctx, cancel := context.WithCancel(context.Background())
	defer func() { cancel(); _ = inner.Close(); l.wg.Wait() }()
	l.wg.Add(1)
	go l.acceptLoop(ctx)
	c, err := net.Dial("tcp", inner.Addr().String())
	if err != nil {
		t.Fatal(err)
	}
	defer c.Close()
	select {
	case <-got:
	case <-time.After(3 * time.Second):
		t.Fatal("listener stopped accepting after a temporary error")
	}
}

// A broker announcing a huge packet is refused before any allocation.
func TestMQTTRefusesOversizedPacket(t *testing.T) {
	a, b := net.Pipe()
	defer a.Close()
	defer b.Close()
	s := &MqttSession{conn: a, Timeout: 2 * time.Second}
	go func() {
		// PUBLISH, remaining length 200 MiB (varint 0x80 0x80 0x80 0x64).
		_, _ = b.Write([]byte{0x30, 0x80, 0x80, 0x80, 0x64})
	}()
	_, err := s.readPacket(context.Background())
	if !errors.Is(err, ErrMQTT) {
		t.Fatalf("err = %v, want ErrMQTT", err)
	}
}
