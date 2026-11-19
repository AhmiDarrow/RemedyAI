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
	"crypto/rand"
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

// TestIrohLivePunch drives the relay-free hole punch end to end: the probe
// learns a STUN mapping, the test swaps encrypted offer/answer over a real
// public MQTT broker exactly as the phone does, and the probe dials only the
// addresses the PC answered with. Needs REMEDY_IROH_LIVE_PUNCH=1 as well.
func TestIrohLivePunch(t *testing.T) {
	bin := os.Getenv("REMEDY_IROH_BIN")
	probe := os.Getenv("REMEDY_IROH_PROBE")
	if bin == "" || probe == "" || os.Getenv("REMEDY_IROH_LIVE_PUNCH") == "" {
		t.Skip("set REMEDY_IROH_BIN, REMEDY_IROH_PROBE and REMEDY_IROH_LIVE_PUNCH=1")
	}
	home := pipeHome(t)
	hostKP := mustHostKP(t, home)
	if os.Getenv("REMEDY_NATIVE_CORE_LIB") == "" {
		defer connect.SetIrohStarterForTest(execIrohStarter)()
	}
	g := connect.NewGateway(nil)
	defer func() { _ = g.Stop() }()
	if err := g.MaybeStart(connect.GatewaySettings{
		Enabled: true, Host: "127.0.0.1", Port: 0, Home: home, Sidecar: 7400,
		Iroh: true, IrohPort: 7498, IrohPunch: true,
	}); err != nil {
		t.Fatal(err)
	}
	deadline := time.Now().Add(30 * time.Second)
	for connect.CurrentIrohAdvert().EndpointID == "" {
		if time.Now().After(deadline) {
			t.Fatal("sidecar never ready")
		}
		time.Sleep(100 * time.Millisecond)
	}
	if connect.CurrentIrohAdvert().PunchBrokers == "" {
		t.Fatal("advert lacks punch brokers")
	}
	_, secret := mustStartPair(t, home, 7401)
	pcID := connect.CurrentIrohAdvert().EndpointID

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	cmd := exec.CommandContext(ctx, probe, pcID, "--punch")
	in, _ := cmd.StdinPipe()
	out, _ := cmd.StdoutPipe()
	if err := cmd.Start(); err != nil {
		t.Fatal(err)
	}
	defer func() { cancel(); _ = cmd.Wait() }()
	sc := bufio.NewScanner(out)
	next := func() map[string]any {
		if !sc.Scan() {
			t.Fatal("probe exited")
		}
		var ev map[string]any
		_ = json.Unmarshal(sc.Bytes(), &ev)
		return ev
	}
	offerEv := next()
	if offerEv["event"] != "punch_offer" {
		t.Fatalf("probe said %v", offerEv)
	}
	var phoneAddrs []string
	for _, a := range offerEv["addrs"].([]any) {
		phoneAddrs = append(phoneAddrs, a.(string))
	}
	t.Logf("phone side STUN offer: %v", phoneAddrs)
	publicOnly := os.Getenv("REMEDY_IROH_PUNCH_PUBLIC_ONLY") != ""
	if publicOnly {
		phoneAddrs = phoneAddrs[:1] // STUN-mapped only: no LAN shortcut
	}

	// Signal over a real public broker, as the phone does.
	var mq *connect.MqttSession
	for _, b := range connect.DefaultPunchBrokers() {
		s, err := connect.NewMqttSession(b.Host, b.Port, 8*time.Second)
		if err != nil {
			continue
		}
		cctx, cc := context.WithTimeout(ctx, 8*time.Second)
		err = s.Connect(cctx)
		cc()
		if err == nil {
			mq = s
			t.Logf("signalling via %s", b.Host)
			break
		}
	}
	if mq == nil {
		t.Skip("no public MQTT broker reachable")
	}
	defer mq.Close()
	answers := make(chan []byte, 4)
	mq.OnMessage = func(_ string, p []byte) { answers <- append([]byte(nil), p...) }
	// Same read window as the PC's responder: above two keepalive pings.
	mq.Timeout = 2*connect.PingEveryS + 5*time.Second
	go mq.PingLoop(ctx)
	go mq.ReaderLoop(ctx)
	var answer connect.PunchAnswer
	t0 := time.Now()
	got := false
	sent := map[string]bool{}
	for attempt := 0; attempt < 5 && !got; attempt++ {
		nb := make([]byte, 8)
		_, _ = rand.Read(nb)
		nonce := hex.EncodeToString(nb)
		sent[nonce] = true
		sctx, sc2 := context.WithTimeout(ctx, 8*time.Second)
		_ = mq.Subscribe(sctx, []string{connect.PunchReplyTopic(hostKP.Public, nonce)}, 1)
		sc2()
		blob, _ := connect.SealPunch(hostKP.Public, connect.PunchOffer{V: 1, From: offerEv["from"].(string),
			Addrs: phoneAddrs, Nonce: nonce, TS: time.Now().Unix()})
		pctx, pc := context.WithTimeout(ctx, 8*time.Second)
		_ = mq.Publish(pctx, connect.PunchTopic(hostKP.Public), blob, 1)
		pc()
		// The responder may still be subscribing when the first offer goes
		// out; an answer to any offer we sent is a good answer.
		wait := time.After(6 * time.Second)
	collect:
		for !got {
			select {
			case a := <-answers:
				if connect.OpenPunch(hostKP.Public, a, &answer) == nil && sent[answer.Nonce] {
					got = true
				}
			case <-wait:
				break collect
			}
		}
	}
	if !got || answer.Error != "" || len(answer.Addrs) == 0 {
		t.Fatalf("no usable answer: got=%v %+v", got, answer)
	}
	t.Logf("PC answered in %v with %v", time.Since(t0).Round(time.Millisecond), answer.Addrs)
	if publicOnly {
		answer.Addrs = answer.Addrs[:1]
	}
	line, _ := json.Marshal(map[string]any{"addrs": answer.Addrs})
	_, _ = in.Write(append(line, '\n'))

	listen := next()
	c, err := net.DialTimeout("tcp", listen["addr"].(string), 2*time.Second)
	if err != nil {
		t.Fatal(err)
	}
	defer c.Close()
	_ = c.SetDeadline(time.Now().Add(30 * time.Second))
	t1 := time.Now()
	client, err := connect.HandshakeInitiator(c, mustKP(t), hostKP.Public, connect.PairPayload(secret, "punch-probe"))
	if err != nil {
		if publicOnly {
			t.Skipf("public-only punch failed (%v): both ends share one router here, and it does not hairpin; "+
				"this case needs the phone on a real mobile network", err)
		}
		t.Fatalf("noise over punched path: %v", err)
	}
	me := liveRequest(t, c, client, 3, connect.HTTPRequest{Method: "GET", Target: "/connect/me"}, nil)
	if !bytes.Contains(me, []byte("HTTP/1.1 200")) {
		t.Fatalf("/connect/me=%q", me)
	}
	dial := next()
	t.Logf("punched connection: path=%v rtt=%vms dial=%vms; handshake+/connect/me %v",
		dial["path"], dial["rtt_ms"], dial["dial_ms"], time.Since(t1).Round(time.Millisecond))
}

func mustKP(t *testing.T) connect.KeyPair {
	t.Helper()
	kp, err := connect.GenerateKeyPair()
	if err != nil {
		t.Fatal(err)
	}
	return kp
}
