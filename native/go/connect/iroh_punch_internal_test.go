package connect

import (
	"bytes"
	"context"
	"errors"
	"io"
	"net"
	"strings"
	"testing"
	"time"
)

var testHostPub = bytes.Repeat([]byte{0x42}, 32)

const testPeer = "32f964994affa248ee05d85780291e07535c4229dfcba0bbde4c44f94594c602"

func TestPunchTopicIsStableAndKeyed(t *testing.T) {
	a := PunchTopic(testHostPub)
	if a != PunchTopic(testHostPub) || !strings.HasPrefix(a, "remedy/punch/") || len(a) != len("remedy/punch/")+32 {
		t.Fatalf("topic %q", a)
	}
	if a == PunchTopic(bytes.Repeat([]byte{0x43}, 32)) {
		t.Fatal("different hosts must not share a topic")
	}
	if PunchReplyTopic(testHostPub, "abcd") != a+"/abcd" {
		t.Fatal("reply topic")
	}
}

func TestSealOpenPunchRoundTrip(t *testing.T) {
	in := PunchAnswer{V: 1, Nonce: "00112233445566778899", Addrs: []string{"203.0.113.7:40000"}}
	blob, err := SealPunch(testHostPub, in)
	if err != nil {
		t.Fatal(err)
	}
	if bytes.Contains(blob, []byte("203.0.113.7")) {
		t.Fatal("address must not appear in the clear")
	}
	var out PunchAnswer
	if err := OpenPunch(testHostPub, blob, &out); err != nil || out.Addrs[0] != in.Addrs[0] {
		t.Fatalf("open: %v %+v", err, out)
	}
	if OpenPunch(bytes.Repeat([]byte{1}, 32), blob, &out) == nil {
		t.Fatal("wrong host key must not decrypt")
	}
	blob[len(blob)-1] ^= 1
	if OpenPunch(testHostPub, blob, &out) == nil {
		t.Fatal("tampered message must not decrypt")
	}
}

func TestCheckOffer(t *testing.T) {
	now := time.Unix(1_800_000_000, 0)
	good := PunchOffer{V: 1, From: testPeer, Addrs: []string{"198.51.100.9:51000"}, Nonce: "0123456789abcdef", TS: now.Unix()}
	if err := checkOffer(good, now); err != nil {
		t.Fatal(err)
	}
	bad := []func(o *PunchOffer){
		func(o *PunchOffer) { o.V = 2 },
		func(o *PunchOffer) { o.From = "short" },
		func(o *PunchOffer) { o.Nonce = "../x" },
		func(o *PunchOffer) { o.TS = now.Add(-10 * time.Minute).Unix() },
		func(o *PunchOffer) { o.Addrs = []string{"example.com:1", "0.0.0.0:5"} },
	}
	for i, mut := range bad {
		o := good
		mut(&o)
		if checkOffer(o, now) == nil {
			t.Fatalf("case %d accepted", i)
		}
	}
}

func TestPunchResponderAnswersOnceAndEncrypts(t *testing.T) {
	now := time.Now()
	var gotPeer string
	var gotAddrs []string
	r := &punchResponder{hostPub: testHostPub, punch: func(_ context.Context, peer string, addrs []string) ([]string, error) {
		gotPeer, gotAddrs = peer, addrs
		return []string{"74.167.84.108:51234", "192.168.0.46:51234", "garbage"}, nil
	}}
	offer, _ := SealPunch(testHostPub, PunchOffer{V: 1, From: testPeer,
		Addrs: []string{"198.51.100.9:51000", "nonsense"}, Nonce: "aaaaaaaaaaaaaaaa", TS: now.Unix()})
	topic, ans, ok := r.handle(context.Background(), offer, now)
	if !ok || topic != PunchReplyTopic(testHostPub, "aaaaaaaaaaaaaaaa") {
		t.Fatalf("ok=%v topic=%q", ok, topic)
	}
	if gotPeer != testPeer || len(gotAddrs) != 1 {
		t.Fatalf("punch called with %q %v", gotPeer, gotAddrs)
	}
	var a PunchAnswer
	if err := OpenPunch(testHostPub, ans, &a); err != nil {
		t.Fatal(err)
	}
	if a.Nonce != "aaaaaaaaaaaaaaaa" || len(a.Addrs) != 2 || a.Addrs[0] != "74.167.84.108:51234" {
		t.Fatalf("answer %+v", a)
	}
	if _, _, ok := r.handle(context.Background(), offer, now); ok {
		t.Fatal("replayed offer must be ignored")
	}
	if _, _, ok := r.handle(context.Background(), []byte("junk from the broker"), now); ok {
		t.Fatal("junk must be ignored")
	}
}

func TestPunchResponderReportsSidecarFailure(t *testing.T) {
	now := time.Now()
	r := &punchResponder{hostPub: testHostPub, punch: func(context.Context, string, []string) ([]string, error) {
		return nil, errors.New("stun failed")
	}}
	offer, _ := SealPunch(testHostPub, PunchOffer{V: 1, From: testPeer,
		Addrs: []string{"198.51.100.9:51000"}, Nonce: "bbbbbbbbbbbbbbbb", TS: now.Unix()})
	_, ans, ok := r.handle(context.Background(), offer, now)
	var a PunchAnswer
	if !ok || OpenPunch(testHostPub, ans, &a) != nil || a.Error == "" || len(a.Addrs) != 0 {
		t.Fatalf("ok=%v answer=%+v", ok, a)
	}
}

func TestPunchIsOptInAndLocked(t *testing.T) {
	if SettingsFromMap(nil).IrohPunch {
		t.Fatal("punch must default off")
	}
	if !SettingsFromMap(map[string]any{"connect_iroh_punch": true}).IrohPunch {
		t.Fatal("setting not read")
	}
	if _, ok := settingsLockKeys["connect_iroh_punch"]; !ok {
		t.Fatal("phone must not be able to turn punching on")
	}
	if _, err := currentIrohPunch(context.Background(), testPeer, nil); err == nil {
		t.Fatal("no sidecar → error")
	}
}

func TestPairQRAdvertisesPunchBrokers(t *testing.T) {
	home := t.TempDir()
	t.Setenv("REMEDY_HOME", home)
	ResetPairStateForTest()
	t.Cleanup(ResetPairStateForTest)
	t.Cleanup(func() { setIrohAdvert(IrohAdvert{}) })
	setIrohAdvert(IrohAdvert{EndpointID: "abc", Addrs: []string{"192.168.0.46:7403"},
		PunchBrokers: PunchBrokerList(DefaultPunchBrokers())})
	qr, err := StartPair(PairStartOpts{Loopback: true, BindHost: "127.0.0.1", BindPort: 7401, Home: home})
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(qr, "\nirp=broker.emqx.io:1883;") {
		t.Fatalf("missing irp= in %q", qr)
	}
}

func TestCGNATVerdictMentionsPunching(t *testing.T) {
	_, off, _ := ClassifyReach(ReachInput{PublicIP: "100.100.1.1"})
	_, on, _ := ClassifyReach(ReachInput{PublicIP: "100.100.1.1", Punch: true})
	if !strings.Contains(off, "Get through carrier-grade NAT") || !strings.Contains(on, "Hole punching is on") {
		t.Fatalf("off=%q on=%q", off, on)
	}
}

// fakeBroker accepts one MQTT client, acks CONNECT and SUBSCRIBE, answers
// PINGREQ, and otherwise stays silent — like an idle offer topic.
func fakeBroker(t *testing.T) (RDVEndpoint, <-chan struct{}) {
	t.Helper()
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = ln.Close() })
	subscribed := make(chan struct{}, 4)
	go func() {
		for {
			c, err := ln.Accept()
			if err != nil {
				return
			}
			go func(c net.Conn) {
				defer c.Close()
				for {
					hdr := make([]byte, 1)
					if _, err := io.ReadFull(c, hdr); err != nil {
						return
					}
					n, mul := 0, 1
					for {
						b := make([]byte, 1)
						if _, err := io.ReadFull(c, b); err != nil {
							return
						}
						n += int(b[0]&0x7f) * mul
						if b[0]&0x80 == 0 {
							break
						}
						mul *= 128
					}
					body := make([]byte, n)
					if _, err := io.ReadFull(c, body); err != nil {
						return
					}
					switch hdr[0] & 0xf0 {
					case 0x10:
						_, _ = c.Write([]byte{0x20, 0x02, 0x00, 0x00})
					case 0x80:
						_, _ = c.Write([]byte{0x90, 0x03, body[0], body[1], 0x01})
						subscribed <- struct{}{}
					case 0xc0:
						_, _ = c.Write([]byte{0xd0, 0x00})
					}
				}
			}(c)
		}
	}()
	addr := ln.Addr().(*net.TCPAddr)
	return RDVEndpoint{Host: "127.0.0.1", Port: addr.Port}, subscribed
}

func TestPunchResponderStaysSubscribedAndStopsPromptly(t *testing.T) {
	if testing.Short() {
		t.Skip("waits past the old 8s read window")
	}
	ep, subscribed := fakeBroker(t)
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan struct{})
	go func() {
		defer close(done)
		RunIrohPunchResponder(ctx, testHostPub, []RDVEndpoint{ep}, func(context.Context, string, []string) ([]string, error) {
			return nil, errors.New("unused")
		})
	}()
	select {
	case <-subscribed:
	case <-time.After(5 * time.Second):
		t.Fatal("never subscribed")
	}
	// The old 8s read timeout dropped and re-made the subscription; a
	// second SUBSCRIBE inside 12s of silence means it is still flapping.
	select {
	case <-subscribed:
		t.Fatal("idle subscription was dropped and re-made")
	case <-time.After(12 * time.Second):
	}
	start := time.Now()
	cancel()
	select {
	case <-done:
	case <-time.After(3 * time.Second):
		t.Fatal("responder did not stop promptly on cancel")
	}
	if d := time.Since(start); d > 2*time.Second {
		t.Fatalf("stop took %v", d)
	}
}
