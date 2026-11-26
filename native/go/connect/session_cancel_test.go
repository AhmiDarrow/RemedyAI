package connect_test

import (
	"context"
	"net"
	"testing"
	"time"

	"github.com/AhmiDarrow/RemedyAI/native/go/connect"
)

// A settings save restarts the relay / rendezvous / iroh dialers and waits for
// each to exit while it holds the settings lock. An idle phone session on one
// of them must end when its context is cancelled, not when the phone next
// speaks or the idle timer runs out, or every settings write stalls.
func TestIdleSessionEndsWhenContextCancelled(t *testing.T) {
	home := pipeHome(t)
	hostKP := mustHostKP(t, home)
	_, secret := mustStartPair(t, home, 7401)
	deviceKP, err := connect.GenerateKeyPair()
	if err != nil {
		t.Fatal(err)
	}

	srv, cli := net.Pipe()
	defer srv.Close()
	defer cli.Close()

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	done := make(chan error, 1)
	go func() {
		_, err := connect.RunSession(ctx, srv, connect.SessionConfig{
			Home:        home,
			HostKP:      hostKP,
			IdleTimeout: 30 * time.Second,
		})
		done <- err
	}()

	client, err := connect.HandshakeInitiator(cli, deviceKP, hostKP.Public, connect.PairPayload(secret, "phone"))
	if err != nil {
		t.Fatal(err)
	}
	// One ping / pong so the session is in its idle loop.
	ping, err := connect.EncodeInner(connect.TypePing, 7, nil, true)
	if err != nil {
		t.Fatal(err)
	}
	blob, err := client.Encrypt(ping)
	if err != nil {
		t.Fatal(err)
	}
	if err := connect.WriteRecord(cli, blob); err != nil {
		t.Fatal(err)
	}
	_ = cli.SetDeadline(time.Now().Add(3 * time.Second))
	if _, _, err := connect.ReadRecord(cli); err != nil {
		t.Fatal(err)
	}
	_ = cli.SetDeadline(time.Time{})
	select {
	case err := <-done:
		t.Fatalf("session ended before cancel: %v", err)
	case <-time.After(300 * time.Millisecond):
	}

	cancel()
	select {
	case <-done:
	case <-time.After(2 * time.Second):
		t.Fatal("idle session kept running after its context was cancelled")
	}
}
