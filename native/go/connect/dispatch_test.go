package connect_test

import (
	"bytes"
	"context"
	"encoding/json"
	"net"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/AhmiDarrow/RemedyAI/native/go/connect"
)

func TestInnerHTTPConnectMe(t *testing.T) {
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

	done := make(chan error, 1)
	go func() {
		_, err := connect.RunSession(context.Background(), srv, connect.SessionConfig{
			Home:        home,
			HostKP:      hostKP,
			IdleTimeout: 3 * time.Second,
		})
		done <- err
	}()

	client, err := connect.HandshakeInitiator(cli, deviceKP, hostKP.Public, connect.PairPayload(secret, "phone"))
	if err != nil {
		t.Fatal(err)
	}

	payload, err := connect.EncodeHTTPRequest(connect.HTTPRequest{
		Method:  "GET",
		Target:  "/connect/me",
		Headers: "",
		Body:    nil,
	})
	if err != nil {
		t.Fatal(err)
	}
	frame, err := connect.EncodeInner(connect.TypeHTTPReq, 3, payload, true)
	if err != nil {
		t.Fatal(err)
	}
	blob, err := client.Encrypt(frame)
	if err != nil {
		t.Fatal(err)
	}
	if err := connect.WriteRecord(cli, blob); err != nil {
		t.Fatal(err)
	}

	_ = cli.SetDeadline(time.Now().Add(3 * time.Second))
	var joined []byte
	for {
		nonce, ct, err := connect.ReadRecord(cli)
		if err != nil {
			t.Fatal(err)
		}
		packed, _ := connect.PackRecord(nonce, ct)
		out, err := client.Decrypt(packed)
		if err != nil {
			t.Fatal(err)
		}
		inner, err := connect.DecodeInner(out)
		if err != nil {
			t.Fatal(err)
		}
		if inner.Type != connect.TypeHTTPRes || inner.ID != 3 {
			t.Fatalf("unexpected %+v", inner)
		}
		joined = append(joined, inner.Payload...)
		if inner.Fin() {
			break
		}
	}
	if !bytes.Contains(joined, []byte("HTTP/1.1 200")) {
		t.Fatalf("joined=%q", joined)
	}
	if !bytes.Contains(joined, []byte(`"device_id"`)) {
		t.Fatalf("missing device_id in %q", joined)
	}
	_ = cli.Close()
	select {
	case <-done:
	case <-time.After(3 * time.Second):
		t.Fatal("idle exit timeout")
	}
}

func TestInnerHTTPDeniesHostPoller(t *testing.T) {
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

	done := make(chan error, 1)
	go func() {
		_, err := connect.RunSession(context.Background(), srv, connect.SessionConfig{
			Home:        home,
			HostKP:      hostKP,
			IdleTimeout: 3 * time.Second,
		})
		done <- err
	}()

	client, err := connect.HandshakeInitiator(cli, deviceKP, hostKP.Public, connect.PairPayload(secret, "phone"))
	if err != nil {
		t.Fatal(err)
	}

	payload, err := connect.EncodeHTTPRequest(connect.HTTPRequest{
		Method: "GET",
		Target: "/api/computer/jobs/next",
	})
	if err != nil {
		t.Fatal(err)
	}
	frame, err := connect.EncodeInner(connect.TypeHTTPReq, 9, payload, true)
	if err != nil {
		t.Fatal(err)
	}
	blob, err := client.Encrypt(frame)
	if err != nil {
		t.Fatal(err)
	}
	if err := connect.WriteRecord(cli, blob); err != nil {
		t.Fatal(err)
	}

	_ = cli.SetDeadline(time.Now().Add(3 * time.Second))
	var joined []byte
	for {
		nonce, ct, err := connect.ReadRecord(cli)
		if err != nil {
			t.Fatal(err)
		}
		packed, _ := connect.PackRecord(nonce, ct)
		out, err := client.Decrypt(packed)
		if err != nil {
			t.Fatal(err)
		}
		inner, err := connect.DecodeInner(out)
		if err != nil {
			t.Fatal(err)
		}
		joined = append(joined, inner.Payload...)
		if inner.Fin() {
			break
		}
	}
	if !bytes.Contains(joined, []byte("403")) || !bytes.Contains(joined, []byte("host-poller")) {
		t.Fatalf("expected host-poller 403, got %q", joined)
	}
	_ = cli.Close()
	select {
	case <-done:
	case <-time.After(3 * time.Second):
	}
}

func TestIterProxyResponseLoopback(t *testing.T) {
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get(connect.ConnectHopHeader) != "1" {
			t.Errorf("missing hop header")
		}
		if !strings.HasPrefix(r.Header.Get("Authorization"), "Bearer ") {
			t.Errorf("missing bearer")
		}
		if r.URL.Path != "/api/ping" {
			t.Errorf("path=%s", r.URL.Path)
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"ok":true}`))
	}))
	defer ts.Close()

	u, err := url.Parse(ts.URL)
	if err != nil {
		t.Fatal(err)
	}
	port, err := strconv.Atoi(u.Port())
	if err != nil {
		t.Fatal(err)
	}

	var chunks [][]byte
	err = connect.IterProxyResponse(connect.ProxyRequest{
		Method:      "GET",
		Path:        "/api/ping",
		SidecarPort: port,
		APIKey:      "test-token",
	}, func(b []byte) error {
		chunks = append(chunks, append([]byte(nil), b...))
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	joined := bytes.Join(chunks, nil)
	if !bytes.Contains(joined, []byte("HTTP/1.1 200")) || !bytes.Contains(joined, []byte(`{"ok":true}`)) {
		t.Fatalf("joined=%q", joined)
	}
	_ = json.Valid([]byte(`{"ok":true}`))
}
