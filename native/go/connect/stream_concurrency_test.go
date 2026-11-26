package connect_test

import (
	"context"
	"net"
	"net/http"
	"net/http/httptest"
	"strconv"
	"testing"
	"time"

	"github.com/AhmiDarrow/RemedyAI/native/go/connect"
)

// While one request streams (a chat turn, /api/events), the session must
// keep reading: a ping still gets its pong. Served inline, the stream held
// the read loop and the phone could not even approve what the turn awaited.
func TestStreamingRequestDoesNotBlockTheSession(t *testing.T) {
	release := make(chan struct{})
	hungUp := make(chan struct{}, 1)
	sidecar := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/event-stream")
		w.WriteHeader(200)
		_, _ = w.Write([]byte("data: hello\n\n"))
		w.(http.Flusher).Flush()
		select {
		case <-release:
		case <-r.Context().Done():
			hungUp <- struct{}{}
		}
	}))
	defer sidecar.Close()
	defer close(release)
	_, portText, _ := net.SplitHostPort(sidecar.Listener.Addr().String())
	port, _ := strconv.Atoi(portText)

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
	go func() {
		_, _ = connect.RunSession(context.Background(), srv, connect.SessionConfig{
			Home: home, HostKP: hostKP, IdleTimeout: 5 * time.Second, SidecarPort: port,
		})
	}()
	client, err := connect.HandshakeInitiator(cli, deviceKP, hostKP.Public, connect.PairPayload(secret, "phone"))
	if err != nil {
		t.Fatal(err)
	}
	send := func(typ byte, id uint32, payload []byte) {
		t.Helper()
		frame, err := connect.EncodeInner(typ, id, payload, true)
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
	}
	req, err := connect.EncodeHTTPRequest(connect.HTTPRequest{Method: "GET", Target: "/api/events"})
	if err != nil {
		t.Fatal(err)
	}
	send(connect.TypeHTTPReq, 3, req)

	_ = cli.SetDeadline(time.Now().Add(4 * time.Second))
	sawStream := false
	for gotPong := false; !gotPong; {
		nonce, ct, err := connect.ReadRecord(cli)
		if err != nil {
			t.Fatalf("no pong while a stream was open (saw stream=%v): %v", sawStream, err)
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
		if inner.Type == connect.TypeHTTPRes && inner.ID == 3 && !sawStream {
			sawStream = true
			send(connect.TypePing, 9, nil)
			continue
		}
		gotPong = inner.Type == connect.TypePong && inner.ID == 9
	}
	// Closing the phone side ends the proxied stream instead of leaving it
	// (and its loopback connection) open forever.
	_ = cli.Close()
	select {
	case <-hungUp:
	case <-time.After(5 * time.Second):
		t.Fatal("the proxied stream outlived the phone session")
	}
}
