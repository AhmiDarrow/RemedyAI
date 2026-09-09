package connect_test

import (
	"bytes"
	"context"
	"crypto/rand"
	"encoding/binary"
	"encoding/hex"
	"net"
	"sync/atomic"
	"testing"
	"time"

	"github.com/AhmiDarrow/RemedyAI/native/go/connect"
)

// Rendezvous publishes this machine on third-party public MQTT brokers. That
// is a reasonable thing to opt into and a poor thing to inherit, so it is off
// unless the owner says otherwise.
func TestRendezvousIsOptIn(t *testing.T) {
	if connect.SettingsFromMap(map[string]any{}).RDV {
		t.Fatal("rendezvous must default off")
	}
	if connect.SettingsFromMap(nil).RDV {
		t.Fatal("rendezvous must default off with no settings at all")
	}
	if !connect.SettingsFromMap(map[string]any{"connect_rdv_enabled": true}).RDV {
		t.Fatal("an explicit opt-in must be honoured")
	}
	if connect.SettingsFromMap(map[string]any{"connect_rdv_enabled": false}).RDV {
		t.Fatal("an explicit opt-out must be honoured")
	}
}

// A stable id per device pair lets a wildcard subscriber on a public broker
// enumerate live machines and keep coming back to the same topic. Mixing a
// time bucket in means a harvested id names nothing an hour later.
func TestRendezvousSessionIDsRotate(t *testing.T) {
	hostPub := make([]byte, 32)
	devicePub := make([]byte, 32)
	if _, err := rand.Read(hostPub); err != nil {
		t.Fatal(err)
	}
	if _, err := rand.Read(devicePub); err != nil {
		t.Fatal(err)
	}

	base := time.Unix(1_700_000_000, 0).UTC()
	bucket := connect.RDVBucket(base)
	now, err := connect.SessionIDDeviceRDV(hostPub, devicePub, bucket)
	if err != nil {
		t.Fatal(err)
	}
	later, err := connect.SessionIDDeviceRDV(hostPub, devicePub, bucket+1)
	if err != nil {
		t.Fatal(err)
	}
	if len(now) != connect.SessionIDLen {
		t.Fatalf("sid len=%d", len(now))
	}
	if bytes.Equal(now, later) {
		t.Fatal("the rendezvous id did not rotate with the bucket")
	}
	// Deterministic within a bucket, or the phone could never meet the PC.
	again, _ := connect.SessionIDDeviceRDV(hostPub, devicePub, bucket)
	if !bytes.Equal(now, again) {
		t.Fatal("the rendezvous id must be stable inside a bucket")
	}
	// Distinct from the relay id: the relay is a host the owner chose, the
	// public brokers are not, and one must not name the other.
	relay, err := connect.SessionIDDevice(hostPub, devicePub)
	if err != nil {
		t.Fatal(err)
	}
	if bytes.Equal(relay, now) {
		t.Fatal("public-broker id must not equal the relay id")
	}
}

// Clocks differ. Near a boundary the PC also holds the neighbouring id so a
// phone a few minutes out still lands on a topic the PC is listening to.
func TestRendezvousBucketsCoverClockSkewAtBoundaries(t *testing.T) {
	mid := time.Unix(1_700_000_000/connect.RDVBucketSeconds*connect.RDVBucketSeconds+connect.RDVBucketSeconds/2, 0)
	if got := connect.RDVBucketsAt(mid); len(got) != 1 {
		t.Fatalf("mid-bucket should hold one id, got %d", len(got))
	}

	start := time.Unix(connect.RDVBucket(mid)*connect.RDVBucketSeconds, 0)
	justAfter := start.Add(time.Minute)
	got := connect.RDVBucketsAt(justAfter)
	if len(got) != 2 || got[1] != connect.RDVBucket(justAfter)-1 {
		t.Fatalf("just after a rollover the previous id must still be held: %v", got)
	}

	justBefore := start.Add(time.Duration(connect.RDVBucketSeconds)*time.Second - time.Minute)
	got = connect.RDVBucketsAt(justBefore)
	if len(got) != 2 || got[1] != connect.RDVBucket(justBefore)+1 {
		t.Fatalf("just before a rollover the next id must already be held: %v", got)
	}
	if len(connect.RDVBucketsAt(mid)) > 2 {
		t.Fatal("never more than two ids per device")
	}
}

// The set the supervisor holds rotates with the clock, so a topic harvested
// today is retired on the next tick after the bucket turns over.
func TestRendezvousSIDSetRotatesForPairedDevices(t *testing.T) {
	home := t.TempDir()
	t.Setenv("REMEDY_HOME", home)
	connect.ResetPairStateForTest()
	t.Cleanup(connect.ResetPairStateForTest)

	devicePub := make([]byte, 32)
	if _, err := rand.Read(devicePub); err != nil {
		t.Fatal(err)
	}
	if _, err := connect.SaveDevice(connect.Device{
		ID:        "0123456789abcdef",
		Name:      "phone",
		PublicHex: hex.EncodeToString(devicePub),
	}, home); err != nil {
		t.Fatal(err)
	}

	mid := time.Unix(1_700_000_000/connect.RDVBucketSeconds*connect.RDVBucketSeconds+connect.RDVBucketSeconds/2, 0)
	first, err := connect.RendezvousSIDsRDV(home, mid)
	if err != nil {
		t.Fatal(err)
	}
	if len(first) != 1 {
		t.Fatalf("one paired device mid-bucket should hold one id, got %d", len(first))
	}
	next, err := connect.RendezvousSIDsRDV(home, mid.Add(time.Duration(connect.RDVBucketSeconds)*time.Second))
	if err != nil {
		t.Fatal(err)
	}
	if len(next) != 1 || bytes.Equal(first[0], next[0]) {
		t.Fatalf("the held id did not rotate: %x -> %x", first, next)
	}

	// The relay set is deliberately not rotated: relay ids are a wire format
	// the paired phone already computes, and the relay is a chosen host.
	relay, err := connect.RendezvousSIDs(home)
	if err != nil {
		t.Fatal(err)
	}
	if len(relay) != 1 {
		t.Fatalf("relay ids=%d", len(relay))
	}
	for _, sid := range append(append([][]byte{}, first...), next...) {
		if bytes.Equal(relay[0], sid) {
			t.Fatal("a public-broker id must never equal the relay id")
		}
	}
}

// Anyone can publish to a public-broker topic, so a refused handshake is not
// evidence the rendezvous is broken. Re-arming keeps the broker session; the
// old behaviour handed a spammer a way to keep the phone off the machine.
func TestRendezvousRearmSurvivesARefusedHandshake(t *testing.T) {
	broker := startFakeBroker(t, 0)
	sid := make([]byte, connect.SessionIDLen)
	if _, err := rand.Read(sid); err != nil {
		t.Fatal(err)
	}

	pc, pcConn := makeRDVSession(t, broker.port(), sid, "pc")
	defer pc.CloseWait()
	phone, phoneConn := makeRDVSession(t, broker.port(), sid, "phone")
	defer phone.CloseWait()
	defer phoneConn.Close()

	// A caller who cannot complete the handshake: the host closes its end.
	_ = pcConn.Close()

	rearmed, err := pc.Rearm()
	if err != nil {
		t.Fatalf("rearm after a refused handshake: %v", err)
	}
	defer rearmed.Close()

	payload := []byte("second-attempt")
	frame := make([]byte, 4+len(payload))
	binary.BigEndian.PutUint32(frame[:4], uint32(len(payload)))
	copy(frame[4:], payload)
	if _, err := phoneConn.Write(frame); err != nil {
		t.Fatal(err)
	}

	_ = rearmed.SetReadDeadline(time.Now().Add(5 * time.Second))
	head := make([]byte, 4)
	if _, err := readFull(rearmed, head); err != nil {
		t.Fatalf("re-armed rendezvous did not deliver: %v", err)
	}
	body := make([]byte, binary.BigEndian.Uint32(head))
	if _, err := readFull(rearmed, body); err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(body, payload) {
		t.Fatalf("payload=%q", body)
	}
}

// The budget: refused handshakes are absorbed on one broker session instead of
// costing a reconnect each, and a run of them eventually gives the broker up.
func TestRendezvousBudgetsBadHandshakesOnOneBrokerSession(t *testing.T) {
	home := t.TempDir()
	t.Setenv("REMEDY_HOME", home)
	connect.ResetPairStateForTest()
	t.Cleanup(connect.ResetPairStateForTest)

	broker := startFakeBroker(t, 0)
	if _, err := connect.StartPair(connect.PairStartOpts{
		Loopback: true,
		BindHost: "127.0.0.1",
		BindPort: 7401,
		Home:     home,
	}); err != nil {
		t.Fatal(err)
	}

	var attempts int64
	refused := make(chan struct{}, 1)
	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()
	go func() {
		_ = connect.RunRDVSupervisor(ctx, connect.RDVSupervisorOpts{
			Home:        home,
			Enabled:     true,
			Interval:    50 * time.Millisecond,
			OpenTimeout: 3 * time.Second,
			Endpoints:   []connect.RDVEndpoint{{Host: "127.0.0.1", Port: broker.port()}},
			Session: func(context.Context, net.Conn) error {
				if atomic.AddInt64(&attempts, 1) == int64(connect.MaxBadHandshakes) {
					select {
					case refused <- struct{}{}:
					default:
					}
				}
				return connect.ErrAuth
			},
		})
	}()

	select {
	case <-refused:
	case <-ctx.Done():
		t.Fatalf("budget never reached: attempts=%d", atomic.LoadInt64(&attempts))
	}
	if got := broker.connections(); got > 2 {
		t.Fatalf("%d refused handshakes cost %d broker connections; they should share one",
			connect.MaxBadHandshakes, got)
	}
	cancel()
}

func readFull(conn net.Conn, buf []byte) (int, error) {
	got := 0
	for got < len(buf) {
		n, err := conn.Read(buf[got:])
		got += n
		if err != nil {
			return got, err
		}
	}
	return got, nil
}
