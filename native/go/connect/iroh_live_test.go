package connect_test

// Live iroh round trip (spike). Skipped unless both binaries are built:
//
//	cd native/iroh && cargo build --release
//	REMEDY_IROH_BIN=$PWD/target/release/remedy-iroh \
//	REMEDY_IROH_PROBE=$PWD/target/release/iroh-probe \
//	go test ./connect -run TestIrohLive -v
//
// It uses n0's public relays over the internet. The probe stands in for the
// phone: it dials the PC's endpoint id and the test runs the real Noise
// initiator through it, so the path is pair → GET /connect/me → streamed
// chat reply, all inside one iroh bidirectional stream.

import (
	"bufio"
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/AhmiDarrow/RemedyAI/native/go/connect"
)

const liveTokens = 40

func TestIrohLiveRoundTrip(t *testing.T) {
	bin := os.Getenv("REMEDY_IROH_BIN")
	probe := os.Getenv("REMEDY_IROH_PROBE")
	if bin == "" || probe == "" {
		t.Skip("set REMEDY_IROH_BIN and REMEDY_IROH_PROBE to run the live iroh round trip")
	}
	home := pipeHome(t)
	hostKP := mustHostKP(t, home)
	if os.Getenv("REMEDY_NATIVE_CORE_LIB") == "" {
		// No Zig remedy_core here: start the sidecar with os/exec (tests only;
		// production spawns through the authorized core path).
		defer connect.SetIrohStarterForTest(execIrohStarter)()
	}

	// Stand-in for the local API: a chat turn streamed as SSE tokens.
	api := httptest.NewUnstartedServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/api/sessions/s1/messages/stream" {
			http.NotFound(w, r)
			return
		}
		w.Header().Set("Content-Type", "text/event-stream")
		fl, _ := w.(http.Flusher)
		for i := 0; i < liveTokens; i++ {
			fmt.Fprintf(w, "event: token\ndata: {\"t\":\"tok%d \"}\n\n", i)
			if fl != nil {
				fl.Flush()
			}
			time.Sleep(10 * time.Millisecond)
		}
		fmt.Fprint(w, "event: done\ndata: {}\n\n")
	}))
	api.Start()
	defer api.Close()
	apiPort, _ := strconv.Atoi(api.URL[strings.LastIndex(api.URL, ":")+1:])

	g := connect.NewGateway(nil)
	defer func() { _ = g.Stop() }()
	if err := g.MaybeStart(connect.GatewaySettings{
		Enabled: true, Host: "127.0.0.1", Port: 0, Home: home,
		APIKey: "tok-iroh-live", Sidecar: apiPort, Iroh: true,
		// Default policy: no relay; a test port so a running Remedy keeps 7402.
		IrohPort: 7499,
	}); err != nil {
		t.Fatal(err)
	}
	deadline := time.Now().Add(45 * time.Second)
	for {
		st := connect.CurrentIrohStatus()
		if connect.CurrentIrohAdvert().EndpointID != "" && st.Verdict != connect.ReachChecking && st.Verdict != connect.ReachOff {
			break
		}
		if time.Now().After(deadline) {
			t.Fatalf("sidecar never finished its self-test: %+v", st)
		}
		time.Sleep(200 * time.Millisecond)
	}
	st := connect.CurrentIrohStatus()
	t.Logf("self-test: verdict=%s protocols=%v mapped=%q public=%q v6=%v — %s",
		st.Verdict, st.MapProtos, st.Mapped, st.PublicIP, st.GlobalV6, st.Message)
	qr, secret := mustStartPair(t, home, 7401)
	irID, ira := "", ""
	for _, line := range strings.Split(qr, "\n") {
		if strings.HasPrefix(line, "ir=") {
			irID = line[3:]
		}
		if strings.HasPrefix(line, "ira=") {
			ira = line[4:]
		}
		if strings.HasPrefix(line, "irr=") || strings.HasPrefix(line, "irm=") {
			t.Fatalf("default QR must not name a relay: %q", line)
		}
	}
	if irID == "" || ira == "" {
		t.Fatalf("QR lacks ir=/ira=: %q", qr)
	}
	t.Logf("PC endpoint id %s, QR addrs %s", irID, ira)
	direct := []string{"--no-relay"}
	for _, a := range strings.Split(ira, ",") {
		direct = append(direct, "--addr", a)
	}

	deviceKP, err := connect.GenerateKeyPair()
	if err != nil {
		t.Fatal(err)
	}
	sum := sha256.Sum256(deviceKP.Public)
	deviceID := hex.EncodeToString(sum[:])[:32]

	modes := []struct {
		name string
		args []string
	}{
		{"qr-addrs-no-relay", direct},
	}
	if st.Mapped != "" {
		// Only the router-mapped public address: from inside the LAN this
		// needs NAT hairpinning, so it is informative, not authoritative.
		modes = append(modes, struct {
			name string
			args []string
		}{"mapped-public-only", []string{"--no-relay", "--addr", st.Mapped}})
	}
	for i, m := range modes {
		payload := connect.HelloPayload(deviceID)
		if i == 0 {
			payload = connect.PairPayload(secret, "iroh-probe")
		}
		if m.name == "mapped-public-only" {
			if err := tryLiveMode(t, probe, irID, m.name, m.args, deviceKP, hostKP.Public, payload); err != nil {
				t.Logf("[%s] not reachable from inside the LAN (router does not hairpin): %v", m.name, err)
			}
			continue
		}
		runLiveMode(t, probe, irID, m.name, m.args, deviceKP, hostKP.Public, payload)
	}
}

func runLiveMode(t *testing.T, probe, id, name string, args []string, deviceKP connect.KeyPair, hostPub, payload []byte) {
	t.Helper()
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	cmd := exec.CommandContext(ctx, probe, append([]string{id}, args...)...)
	out, err := cmd.StdoutPipe()
	if err != nil {
		t.Fatal(err)
	}
	if err := cmd.Start(); err != nil {
		t.Fatal(err)
	}
	defer func() { cancel(); _ = cmd.Wait() }()
	events := make(chan map[string]any, 16)
	go func() {
		sc := bufio.NewScanner(out)
		for sc.Scan() {
			var ev map[string]any
			if json.Unmarshal(sc.Bytes(), &ev) == nil {
				events <- ev
			}
		}
		close(events)
	}()
	var addr string
	select {
	case ev := <-events:
		addr, _ = ev["addr"].(string)
	case <-time.After(30 * time.Second):
		t.Fatalf("%s: probe never listened", name)
	}

	t0 := time.Now()
	c, err := net.DialTimeout("tcp", addr, 2*time.Second)
	if err != nil {
		t.Fatal(err)
	}
	defer c.Close()
	_ = c.SetDeadline(time.Now().Add(60 * time.Second))
	client, err := connect.HandshakeInitiator(c, deviceKP, hostPub, payload)
	if err != nil {
		t.Fatalf("%s: noise over iroh: %v", name, err)
	}
	handshake := time.Since(t0)

	t1 := time.Now()
	me := liveRequest(t, c, client, 3, connect.HTTPRequest{Method: "GET", Target: "/connect/me"}, nil)
	meRTT := time.Since(t1)
	if !bytes.Contains(me, []byte("HTTP/1.1 200")) || !bytes.Contains(me, []byte(`"device_id"`)) {
		t.Fatalf("%s: /connect/me=%q", name, me)
	}

	t2 := time.Now()
	var first time.Duration
	body := liveRequest(t, c, client, 5, connect.HTTPRequest{
		Method:  "POST",
		Target:  "/api/sessions/s1/messages/stream",
		Headers: "Content-Type: application/json\r\n",
		Body:    []byte(`{"content":"hi"}`),
	}, func(chunk []byte) {
		if first == 0 && bytes.Contains(chunk, []byte("tok0")) {
			first = time.Since(t2)
		}
	})
	total := time.Since(t2)
	if got := bytes.Count(body, []byte("event: token")); got != liveTokens {
		t.Fatalf("%s: streamed %d tokens, want %d", name, got, liveTokens)
	}

	dial := map[string]any{}
	var paths []string
	drain := time.After(300 * time.Millisecond)
loop:
	for {
		select {
		case ev, ok := <-events:
			if !ok {
				break loop
			}
			switch ev["event"] {
			case "dialed":
				dial = ev
			case "path":
				paths = append(paths, fmt.Sprintf("%v@%vms(rtt %vms)", ev["path"], ev["after_ms"], ev["rtt_ms"]))
			}
		case <-drain:
			break loop
		}
	}
	t.Logf("[%s] iroh dial %vms path=%v rtt=%vms; noise handshake (incl. dial) %v; /connect/me %v; "+
		"chat stream first token %v, %d tokens in %v; path changes %v",
		name, dial["dial_ms"], dial["path"], dial["rtt_ms"], handshake.Round(time.Millisecond),
		meRTT.Round(time.Millisecond), first.Round(time.Millisecond), liveTokens,
		total.Round(time.Millisecond), paths)
	if dial["path"] == "relay" {
		t.Fatalf("%s: no-relay probe reported a relay path", name)
	}
}

// tryLiveMode is a quick reachability check: dial and handshake only.
func tryLiveMode(t *testing.T, probe, id, name string, args []string, deviceKP connect.KeyPair, hostPub, payload []byte) error {
	t.Helper()
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	cmd := exec.CommandContext(ctx, probe, append([]string{id}, args...)...)
	out, err := cmd.StdoutPipe()
	if err != nil {
		return err
	}
	if err := cmd.Start(); err != nil {
		return err
	}
	defer func() { cancel(); _ = cmd.Wait() }()
	sc := bufio.NewScanner(out)
	if !sc.Scan() {
		return fmt.Errorf("probe exited")
	}
	var ev map[string]any
	_ = json.Unmarshal(sc.Bytes(), &ev)
	addr, _ := ev["addr"].(string)
	c, err := net.DialTimeout("tcp", addr, 2*time.Second)
	if err != nil {
		return err
	}
	defer c.Close()
	_ = c.SetDeadline(time.Now().Add(12 * time.Second))
	t0 := time.Now()
	if _, err := connect.HandshakeInitiator(c, deviceKP, hostPub, payload); err != nil {
		return err
	}
	t.Logf("[%s] handshake over the router-mapped public address in %v", name, time.Since(t0).Round(time.Millisecond))
	return nil
}

func execIrohStarter(ctx context.Context, _ string, bin string) (*connect.IrohProcess, error) {
	cmd := exec.CommandContext(ctx, bin)
	stdin, err := cmd.StdinPipe()
	if err != nil {
		return nil, err
	}
	stdout, err := cmd.StdoutPipe()
	if err != nil {
		return nil, err
	}
	if err := cmd.Start(); err != nil {
		return nil, err
	}
	return &connect.IrohProcess{
		Stdin:  stdin,
		Stdout: stdout,
		Wait:   cmd.Wait,
		Kill:   func() { _ = cmd.Process.Kill() },
	}, nil
}

// liveRequest sends one inner HTTP request and collects its response frames.
func liveRequest(t *testing.T, c net.Conn, client *connect.SessionCrypto, id uint32, req connect.HTTPRequest, onChunk func([]byte)) []byte {
	t.Helper()
	payload, err := connect.EncodeHTTPRequest(req)
	if err != nil {
		t.Fatal(err)
	}
	frame, err := connect.EncodeInner(connect.TypeHTTPReq, id, payload, true)
	if err != nil {
		t.Fatal(err)
	}
	blob, err := client.Encrypt(frame)
	if err != nil {
		t.Fatal(err)
	}
	if err := connect.WriteRecord(c, blob); err != nil {
		t.Fatal(err)
	}
	var joined []byte
	for {
		nonce, ct, err := connect.ReadRecord(c)
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
		if inner.Type != connect.TypeHTTPRes || inner.ID != id {
			continue // pings / rekeys
		}
		joined = append(joined, inner.Payload...)
		if onChunk != nil {
			onChunk(inner.Payload)
		}
		if inner.Fin() {
			return joined
		}
	}
}
