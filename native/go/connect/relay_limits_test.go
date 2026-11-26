package connect

import (
	"net"
	"testing"
	"time"
)

// Idle clients that never send a session id cannot pile up past the cap:
// the relay closes the extra socket straight away.
func TestRelayClosesClientsPastTheCap(t *testing.T) {
	old := relayMaxClients
	relayMaxClients = 2
	defer func() { relayMaxClients = old }()
	r, err := StartRelay("127.0.0.1", 0, time.Second)
	if err != nil {
		t.Fatal(err)
	}
	defer r.Stop()
	var held []net.Conn
	defer func() {
		for _, c := range held {
			_ = c.Close()
		}
	}()
	for i := 0; i < 2; i++ {
		c, err := net.Dial("tcp", r.Addr())
		if err != nil {
			t.Fatal(err)
		}
		held = append(held, c)
	}
	time.Sleep(300 * time.Millisecond) // let the relay accept both
	extra, err := net.Dial("tcp", r.Addr())
	if err != nil {
		t.Fatal(err)
	}
	defer extra.Close()
	_ = extra.SetReadDeadline(time.Now().Add(2 * time.Second))
	buf := make([]byte, 1)
	_, err = extra.Read(buf)
	if ne, ok := err.(net.Error); ok && ne.Timeout() {
		t.Fatal("relay kept a client past its cap")
	}
}
