package connect

import (
	"bytes"
	"context"
	"encoding/binary"
	"io"
	"net"
	"testing"
	"time"
)

// A quiet rendezvous subscription must still deliver a phone record that
// arrives after a silence longer than the dial loop's open timeout.
func TestRDVSubscriptionSurvivesIdle(t *testing.T) {
	if testing.Short() {
		t.Skip("waits past the open timeout")
	}
	ep, subscribed, clients := fakeBroker(t)
	sid := bytes.Repeat([]byte{0x07}, 16)
	got := make(chan []byte, 1)
	run := func(ctx context.Context, conn net.Conn) error {
		hdr := make([]byte, 4)
		if _, err := io.ReadFull(conn, hdr); err != nil {
			return err
		}
		body := make([]byte, binary.BigEndian.Uint32(hdr))
		if _, err := io.ReadFull(conn, body); err != nil {
			return err
		}
		got <- body
		<-ctx.Done()
		return ctx.Err()
	}
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan struct{})
	go func() {
		defer close(done)
		runRDVDialLoop(ctx, t.TempDir(), sid, []RDVEndpoint{ep}, time.Second, run)
	}()
	select {
	case <-subscribed:
	case <-time.After(5 * time.Second):
		t.Fatal("never subscribed")
	}
	c := <-clients
	// Three open timeouts of silence, still short of the first PINGREQ.
	time.Sleep(3 * time.Second)
	topic, err := RDVTopic(sid, "phone")
	if err != nil {
		t.Fatal(err)
	}
	pkt, err := BuildPublish(0, topic, []byte("hello"), 0)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := c.Write(pkt); err != nil {
		t.Fatalf("subscription was dropped during the silence: %v", err)
	}
	select {
	case b := <-got:
		if string(b) != "hello" {
			t.Fatalf("got %q", b)
		}
	case <-time.After(3 * time.Second):
		t.Fatal("record published after an idle spell never reached the handshake")
	}
	cancel()
	select {
	case <-done:
	case <-time.After(3 * time.Second):
		t.Fatal("dial loop did not stop promptly on cancel")
	}
}
