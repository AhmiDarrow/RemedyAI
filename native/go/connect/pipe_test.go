package connect_test

import (
	"bytes"
	"context"
	"encoding/hex"
	"errors"
	"net"
	"strconv"
	"sync"
	"testing"
	"time"

	"github.com/AhmiDarrow/RemedyAI/native/go/connect"
)

func pipeHome(t *testing.T) string {
	t.Helper()
	home := t.TempDir()
	t.Setenv("REMEDY_HOME", home)
	connect.ResetPairStateForTest()
	t.Cleanup(connect.ResetPairStateForTest)
	_ = connect.SetPaused(false, home)
	return home
}

func mustHostKP(t *testing.T, home string) connect.KeyPair {
	t.Helper()
	kp, err := connect.LoadOrCreateHostKeyPair(home)
	if err != nil {
		t.Fatal(err)
	}
	return kp
}

func mustStartPair(t *testing.T, home string, port int) (qr string, secret []byte) {
	t.Helper()
	qr, err := connect.StartPair(connect.PairStartOpts{
		Loopback: true,
		BindHost: "127.0.0.1",
		BindPort: port,
		Home:     home,
	})
	if err != nil {
		t.Fatal(err)
	}
	secret, err = connect.ParsePairSecret(qr)
	if err != nil {
		t.Fatal(err)
	}
	return qr, secret
}

func TestAuthenticateHelloRequiresStoredPublicKey(t *testing.T) {
	home := pipeHome(t)
	_, err := connect.SaveDevice(connect.Device{
		ID:        "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
		Name:      "p",
		PublicHex: "",
		Revoked:   false,
	}, home)
	if err != nil {
		t.Fatal(err)
	}
	_, err = connect.AuthenticatePayload(connect.HelloPayload("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"), bytesFilledPub(0x22), home)
	if !errors.Is(err, connect.ErrAuth) {
		t.Fatalf("expected ErrAuth, got %v", err)
	}
}

func TestAuthenticateHelloMatchingKeyAccepted(t *testing.T) {
	home := pipeHome(t)
	pub := bytesFilledPub(0x33)
	id := "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
	if _, err := connect.SaveDevice(connect.Device{
		ID:        id,
		Name:      "p",
		PublicHex: hex.EncodeToString(pub),
	}, home); err != nil {
		t.Fatal(err)
	}
	rec, err := connect.AuthenticatePayload(connect.HelloPayload(id), pub, home)
	if err != nil || rec == nil || rec.ID != id {
		t.Fatalf("rec=%+v err=%v", rec, err)
	}
}

func TestAuthenticateHelloStaticMismatchFamily(t *testing.T) {
	home := pipeHome(t)
	id := "cccccccccccccccccccccccccccccccc"
	if _, err := connect.SaveDevice(connect.Device{
		ID:        id,
		Name:      "p",
		PublicHex: hex.EncodeToString(bytesFilledPub(0x11)),
	}, home); err != nil {
		t.Fatal(err)
	}
	cases := []struct {
		name string
		pub  []byte
	}{
		{"wrong_static", bytesFilledPub(0x22)},
		{"nil_static", nil},
		{"short_static", []byte{0x01, 0x02}},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			_, err := connect.AuthenticatePayload(connect.HelloPayload(id), tc.pub, home)
			if !errors.Is(err, connect.ErrAuth) {
				t.Fatalf("got %v", err)
			}
		})
	}
}

func TestAuthenticatePairEnvelopeAndRawSecret(t *testing.T) {
	home := pipeHome(t)
	_, secret := mustStartPair(t, home, 7401)
	pub := bytesFilledPub(0x77)
	rec, err := connect.AuthenticatePayload(secret, pub, home)
	if err != nil || rec == nil {
		t.Fatalf("raw: %+v err=%v", rec, err)
	}
	n, _ := connect.ActiveDeviceCount(home)
	if n != 1 {
		t.Fatalf("active=%d", n)
	}

	// Consumed secret + same static still reconnects (allowlisted).
	rec2, err := connect.AuthenticatePayload(connect.PairPayload(secret, "phone"), pub, home)
	if err != nil || rec2 == nil || rec2.ID != rec.ID {
		t.Fatalf("reconnect: %+v err=%v", rec2, err)
	}
}

func TestAuthenticateUnknownDeviceFailsClosed(t *testing.T) {
	home := pipeHome(t)
	_, err := connect.AuthenticatePayload(connect.HelloPayload("dddddddddddddddddddddddddddddddd"), bytesFilledPub(0x44), home)
	if !errors.Is(err, connect.ErrAuth) {
		t.Fatalf("got %v", err)
	}
}

func TestNoiseHandshakeAndRecordRoundtripOverTCP(t *testing.T) {
	home := pipeHome(t)
	hostKP := mustHostKP(t, home)

	authed := make(chan connect.Device, 1)
	gotPlain := make(chan []byte, 1)
	l := connect.NewListener()
	defer func() { _ = l.Stop() }()

	cfg := connect.SessionConfig{
		Home:     home,
		HostKP:   hostKP,
		Listener: l,
		OnDevice: func(d connect.Device) { authed <- d },
		OnAuthed: func(ctx context.Context, sess *connect.Session) error {
			plain, err := sess.RecvPlain()
			if err != nil {
				return err
			}
			gotPlain <- plain
			return sess.SendPlain([]byte(`HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\n{}`))
		},
	}
	if err := l.Start("127.0.0.1", 0, connect.SessionConnHandler(cfg)); err != nil {
		t.Fatal(err)
	}
	host, port, ok := l.ListeningAddr()
	if !ok {
		t.Fatal("not listening")
	}
	_, secret := mustStartPair(t, home, port)

	deviceKP, err := connect.GenerateKeyPair()
	if err != nil {
		t.Fatal(err)
	}
	c, err := net.DialTimeout("tcp", net.JoinHostPort(host, strconv.Itoa(port)), time.Second)
	if err != nil {
		t.Fatal(err)
	}
	defer c.Close()
	_ = c.SetDeadline(time.Now().Add(5 * time.Second))

	client, err := connect.HandshakeInitiator(c, deviceKP, hostKP.Public, connect.PairPayload(secret, "phone"))
	if err != nil {
		t.Fatal(err)
	}
	req := []byte("GET /connect/me HTTP/1.1\r\nHost: x\r\nContent-Length: 0\r\n\r\n")
	blob, err := client.Encrypt(req)
	if err != nil {
		t.Fatal(err)
	}
	if err := connect.WriteRecord(c, blob); err != nil {
		t.Fatal(err)
	}

	select {
	case d := <-authed:
		if d.ID == "" || d.Name != "phone" {
			t.Fatalf("device=%+v", d)
		}
		deadline := time.Now().Add(2 * time.Second)
		for time.Now().Before(deadline) && l.DeviceID(nil) == "" {
			if l.LiveCount() >= 1 {
				break
			}
			time.Sleep(5 * time.Millisecond)
		}
	case <-time.After(3 * time.Second):
		t.Fatal("auth timeout")
	}

	select {
	case plain := <-gotPlain:
		if !bytes.Equal(plain, req) {
			t.Fatalf("host saw %q", plain)
		}
	case <-time.After(3 * time.Second):
		t.Fatal("plain timeout")
	}

	nonce, ct, err := connect.ReadRecord(c)
	if err != nil {
		t.Fatal(err)
	}
	packed, err := connect.PackRecord(nonce, ct)
	if err != nil {
		t.Fatal(err)
	}
	resp, err := client.Decrypt(packed)
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Contains(resp, []byte("200")) {
		t.Fatalf("resp=%q", resp)
	}
}

func TestAndroidRawSecretHandshakeOverTCP(t *testing.T) {
	home := pipeHome(t)
	hostKP := mustHostKP(t, home)
	done := make(chan connect.Device, 1)

	l := connect.NewListener()
	defer func() { _ = l.Stop() }()
	cfg := connect.SessionConfig{
		Home:     home,
		HostKP:   hostKP,
		Listener: l,
		OnAuthed: func(ctx context.Context, sess *connect.Session) error {
			done <- sess.Device
			plain, err := sess.RecvPlain()
			if err != nil {
				return err
			}
			if string(plain) != "ping" {
				return errors.New("unexpected plain")
			}
			return sess.SendPlain([]byte("pong"))
		},
	}
	if err := l.Start("127.0.0.1", 0, connect.SessionConnHandler(cfg)); err != nil {
		t.Fatal(err)
	}
	host, port, ok := l.ListeningAddr()
	if !ok {
		t.Fatal("not listening")
	}
	_, secret := mustStartPair(t, home, port)
	deviceKP, err := connect.GenerateKeyPair()
	if err != nil {
		t.Fatal(err)
	}
	c, err := net.DialTimeout("tcp", net.JoinHostPort(host, strconv.Itoa(port)), time.Second)
	if err != nil {
		t.Fatal(err)
	}
	defer c.Close()
	_ = c.SetDeadline(time.Now().Add(5 * time.Second))

	// Android first payload is the raw 32-byte pair secret.
	client, err := connect.HandshakeInitiator(c, deviceKP, hostKP.Public, secret)
	if err != nil {
		t.Fatal(err)
	}
	select {
	case d := <-done:
		if d.ID == "" {
			t.Fatal("empty device")
		}
	case <-time.After(3 * time.Second):
		t.Fatal("auth timeout")
	}

	blob, err := client.Encrypt([]byte("ping"))
	if err != nil {
		t.Fatal(err)
	}
	if err := connect.WriteRecord(c, blob); err != nil {
		t.Fatal(err)
	}
	nonce, ct, err := connect.ReadRecord(c)
	if err != nil {
		t.Fatal(err)
	}
	packed, _ := connect.PackRecord(nonce, ct)
	out, err := client.Decrypt(packed)
	if err != nil || string(out) != "pong" {
		t.Fatalf("out=%q err=%v", out, err)
	}
}

func TestAllowlistedReconnectAfterSecretConsumed(t *testing.T) {
	home := pipeHome(t)
	hostKP := mustHostKP(t, home)
	_, secret := mustStartPair(t, home, 7401)
	deviceKP, err := connect.GenerateKeyPair()
	if err != nil {
		t.Fatal(err)
	}
	id, err := connect.CompletePair(secret, deviceKP.Public, "phone", home)
	if err != nil || id == "" {
		t.Fatalf("pre-pair: %v", err)
	}

	ready := make(chan struct{})
	l := connect.NewListener()
	defer func() { _ = l.Stop() }()
	cfg := connect.SessionConfig{
		Home:     home,
		HostKP:   hostKP,
		Listener: l,
		OnAuthed: func(ctx context.Context, sess *connect.Session) error {
			close(ready)
			if sess.Device.ID != id {
				return errors.New("wrong device")
			}
			_, err := sess.RecvPlain()
			return err
		},
	}
	if err := l.Start("127.0.0.1", 0, connect.SessionConnHandler(cfg)); err != nil {
		t.Fatal(err)
	}
	host, port, ok := l.ListeningAddr()
	if !ok {
		t.Fatal("not listening")
	}
	c, err := net.DialTimeout("tcp", net.JoinHostPort(host, strconv.Itoa(port)), time.Second)
	if err != nil {
		t.Fatal(err)
	}
	defer c.Close()
	_ = c.SetDeadline(time.Now().Add(5 * time.Second))

	client, err := connect.HandshakeInitiator(c, deviceKP, hostKP.Public, secret)
	if err != nil {
		t.Fatal(err)
	}
	select {
	case <-ready:
	case <-time.After(3 * time.Second):
		t.Fatal("reconnect auth timeout")
	}
	blob, err := client.Encrypt([]byte("hi"))
	if err != nil {
		t.Fatal(err)
	}
	if err := connect.WriteRecord(c, blob); err != nil {
		t.Fatal(err)
	}
}

func TestHelloReconnectOverTCP(t *testing.T) {
	home := pipeHome(t)
	hostKP := mustHostKP(t, home)
	deviceKP, err := connect.GenerateKeyPair()
	if err != nil {
		t.Fatal(err)
	}
	_, secret := mustStartPair(t, home, 7401)
	id, err := connect.CompletePair(secret, deviceKP.Public, "kitchen", home)
	if err != nil {
		t.Fatal(err)
	}

	got := make(chan string, 1)
	l := connect.NewListener()
	defer func() { _ = l.Stop() }()
	cfg := connect.SessionConfig{
		Home:     home,
		HostKP:   hostKP,
		Listener: l,
		OnAuthed: func(ctx context.Context, sess *connect.Session) error {
			got <- sess.Device.ID
			return nil
		},
	}
	if err := l.Start("127.0.0.1", 0, connect.SessionConnHandler(cfg)); err != nil {
		t.Fatal(err)
	}
	host, port, ok := l.ListeningAddr()
	if !ok {
		t.Fatal("not listening")
	}
	c, err := net.DialTimeout("tcp", net.JoinHostPort(host, strconv.Itoa(port)), time.Second)
	if err != nil {
		t.Fatal(err)
	}
	defer c.Close()
	_ = c.SetDeadline(time.Now().Add(5 * time.Second))

	if _, err := connect.HandshakeInitiator(c, deviceKP, hostKP.Public, connect.HelloPayload(id)); err != nil {
		t.Fatal(err)
	}
	select {
	case gotID := <-got:
		if gotID != id {
			t.Fatalf("id=%s want %s", gotID, id)
		}
	case <-time.After(3 * time.Second):
		t.Fatal("hello timeout")
	}
}

func TestPausedRefusesSession(t *testing.T) {
	home := pipeHome(t)
	hostKP := mustHostKP(t, home)
	if err := connect.SetPaused(true, home); err != nil {
		t.Fatal(err)
	}
	a, b := net.Pipe()
	defer a.Close()
	defer b.Close()

	errCh := make(chan error, 1)
	go func() {
		_, err := connect.AcceptSession(context.Background(), a, connect.SessionConfig{
			Home:   home,
			HostKP: hostKP,
		})
		errCh <- err
	}()
	select {
	case err := <-errCh:
		if !errors.Is(err, connect.ErrPaused) {
			t.Fatalf("got %v", err)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("timeout")
	}
	_ = b
}

func TestHandshakeTimeoutFailClosed(t *testing.T) {
	home := pipeHome(t)
	hostKP := mustHostKP(t, home)
	a, b := net.Pipe()
	defer a.Close()
	defer b.Close()

	errCh := make(chan error, 1)
	go func() {
		_, err := connect.AcceptSession(context.Background(), a, connect.SessionConfig{
			Home:             home,
			HostKP:           hostKP,
			HandshakeTimeout: 50 * time.Millisecond,
		})
		errCh <- err
	}()
	// Peer never sends msg0.
	select {
	case err := <-errCh:
		if err == nil {
			t.Fatal("expected timeout/error")
		}
	case <-time.After(2 * time.Second):
		t.Fatal("AcceptSession did not return")
	}
}

func TestWrongPairSecretFailsAuth(t *testing.T) {
	home := pipeHome(t)
	hostKP := mustHostKP(t, home)
	mustStartPair(t, home, 7401)
	wrong := bytesFilledPub(0x09)

	l := connect.NewListener()
	defer func() { _ = l.Stop() }()
	var once sync.Once
	failed := make(chan struct{})
	cfg := connect.SessionConfig{
		Home:   home,
		HostKP: hostKP,
		OnAuthed: func(ctx context.Context, sess *connect.Session) error {
			t.Error("must not authed")
			return nil
		},
	}
	// Wrap handler to observe AcceptSession failure via closed conn without auth.
	handler := func(ctx context.Context, conn net.Conn) {
		_, err := connect.AcceptSession(ctx, conn, cfg)
		if err != nil {
			once.Do(func() { close(failed) })
		}
	}
	if err := l.Start("127.0.0.1", 0, handler); err != nil {
		t.Fatal(err)
	}
	host, port, ok := l.ListeningAddr()
	if !ok {
		t.Fatal("not listening")
	}
	deviceKP, err := connect.GenerateKeyPair()
	if err != nil {
		t.Fatal(err)
	}
	c, err := net.DialTimeout("tcp", net.JoinHostPort(host, strconv.Itoa(port)), time.Second)
	if err != nil {
		t.Fatal(err)
	}
	defer c.Close()
	_ = c.SetDeadline(time.Now().Add(3 * time.Second))
	// Handshake may succeed cryptographically; auth must fail on wrong PS.
	_, err = connect.HandshakeInitiator(c, deviceKP, hostKP.Public, wrong)
	if err != nil {
		// Some stacks fail earlier; either way session must not authenticate.
		select {
		case <-failed:
		case <-time.After(2 * time.Second):
		}
		return
	}
	select {
	case <-failed:
	case <-time.After(3 * time.Second):
		t.Fatal("expected auth failure")
	}
}

func TestNetPipeHandshakeBothDirections(t *testing.T) {
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

	type result struct {
		sess *connect.Session
		err  error
	}
	ch := make(chan result, 1)
	go func() {
		sess, err := connect.AcceptSession(context.Background(), srv, connect.SessionConfig{
			Home:   home,
			HostKP: hostKP,
		})
		ch <- result{sess, err}
	}()

	client, err := connect.HandshakeInitiator(cli, deviceKP, hostKP.Public, connect.PairPayload(secret, "desk"))
	if err != nil {
		t.Fatal(err)
	}
	res := <-ch
	if res.err != nil || res.sess == nil {
		t.Fatalf("accept: %+v", res.err)
	}
	if res.sess.Device.Name != "desk" {
		t.Fatalf("name=%q", res.sess.Device.Name)
	}

	// net.Pipe Write blocks until the peer reads — run opposite sides concurrently.
	errHost := make(chan error, 1)
	go func() { errHost <- res.sess.SendPlain([]byte("host-to-phone")) }()
	nonce, ct, err := connect.ReadRecord(cli)
	if err != nil {
		t.Fatal(err)
	}
	if err := <-errHost; err != nil {
		t.Fatal(err)
	}
	packed, _ := connect.PackRecord(nonce, ct)
	got, err := client.Decrypt(packed)
	if err != nil || string(got) != "host-to-phone" {
		t.Fatalf("got=%q err=%v", got, err)
	}

	errPhone := make(chan error, 1)
	var back []byte
	go func() {
		var rerr error
		back, rerr = res.sess.RecvPlain()
		errPhone <- rerr
	}()
	blob, err := client.Encrypt([]byte("phone-to-host"))
	if err != nil {
		t.Fatal(err)
	}
	if err := connect.WriteRecord(cli, blob); err != nil {
		t.Fatal(err)
	}
	if err := <-errPhone; err != nil || string(back) != "phone-to-host" {
		t.Fatalf("back=%q err=%v", back, err)
	}
}

func bytesFilledPub(b byte) []byte {
	out := make([]byte, 32)
	for i := range out {
		out[i] = b
	}
	return out
}
