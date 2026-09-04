package connect

import (
	"encoding/hex"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func pairHome(t *testing.T) string {
	t.Helper()
	home := t.TempDir()
	t.Setenv("REMEDY_HOME", home)
	ResetPairStateForTest()
	t.Cleanup(ResetPairStateForTest)
	return home
}

func mustQR(t *testing.T, home string) string {
	t.Helper()
	qr, err := StartPair(PairStartOpts{
		Loopback: true,
		BindHost: "127.0.0.1",
		BindPort: 7401,
		Home:     home,
	})
	if err != nil {
		t.Fatal(err)
	}
	return qr
}

func TestStartPairRefusesNonLoopback(t *testing.T) {
	home := pairHome(t)
	_, err := StartPair(PairStartOpts{Loopback: false, BindHost: "127.0.0.1", BindPort: 7401, Home: home})
	if err == nil || !strings.Contains(err.Error(), "loopback") {
		t.Fatalf("expected loopback error, got %v", err)
	}
}

func TestPendingPairRendezvousAndSIDs(t *testing.T) {
	home := pairHome(t)
	sid, err := PendingPairRendezvous(home)
	if err != nil || sid != nil {
		t.Fatalf("empty pending: sid=%v err=%v", sid, err)
	}
	_ = mustQR(t, home)
	sid, err = PendingPairRendezvous(home)
	if err != nil || len(sid) != SessionIDLen {
		t.Fatalf("pending sid len=%d err=%v", len(sid), err)
	}
	all, err := RendezvousSIDs(home)
	if err != nil || len(all) != 1 || string(all[0]) != string(sid) {
		t.Fatalf("sids=%v err=%v", all, err)
	}

	// Paired device adds a second sid.
	secret := PendingSecretForTest()
	devPub := bytesFilled(0x22)
	if _, err := CompletePair(secret, devPub, "phone", home); err != nil {
		t.Fatal(err)
	}
	all, err = RendezvousSIDs(home)
	if err != nil || len(all) != 1 {
		// Pair window consumed by CompletePair — only the device sid remains.
		t.Fatalf("after pair: len=%d err=%v", len(all), err)
	}
	kp, err := LoadOrCreateHostKeyPair(home)
	if err != nil {
		t.Fatal(err)
	}
	want, err := SessionIDDevice(kp.Public, devPub)
	if err != nil {
		t.Fatal(err)
	}
	if string(all[0]) != string(want) {
		t.Fatalf("device sid mismatch")
	}
}

func TestQRHasNoLocalAPITokenOrBearer(t *testing.T) {
	home := pairHome(t)
	t.Setenv("REMEDY_API_KEY", "not-a-portal-secret-dummy-key")
	qr := mustQR(t, home)
	low := strings.ToLower(qr)
	for _, want := range []string{QRVersion, "hp=", "ps=", "lan=127.0.0.1:7401", "exp="} {
		if !strings.Contains(qr, want) {
			t.Fatalf("QR missing %q:\n%s", want, qr)
		}
	}
	for _, bad := range []string{"local_api_token", "bearer", "api_key", "authorization", "not-a-portal-secret-dummy-key"} {
		if strings.Contains(low, bad) {
			t.Fatalf("QR must not contain %q", bad)
		}
	}
}

func TestExpiredQRFailClosed(t *testing.T) {
	home := pairHome(t)
	base := time.Unix(1_700_000_000, 0)
	nowFunc = func() time.Time { return base }
	t.Cleanup(func() { nowFunc = time.Now })

	qr := mustQR(t, home)
	secret, err := ParsePairSecret(qr)
	if err != nil {
		t.Fatal(err)
	}
	nowFunc = func() time.Time { return base.Add(61 * time.Second) }
	_, err = CompletePair(secret, bytesFilled(0x11), "phone", home)
	if err == nil || !strings.Contains(err.Error(), "expired") {
		t.Fatalf("expected expired, got %v", err)
	}
	n, err := ActiveDeviceCount(home)
	if err != nil || n != 0 {
		t.Fatalf("active=%d err=%v", n, err)
	}
}

func TestReusedQRFailClosed(t *testing.T) {
	home := pairHome(t)
	qr := mustQR(t, home)
	secret, err := ParsePairSecret(qr)
	if err != nil {
		t.Fatal(err)
	}
	id, err := CompletePair(secret, bytesFilled(0x22), "phone-a", home)
	if err != nil || id == "" {
		t.Fatalf("first pair: id=%q err=%v", id, err)
	}
	_, err = CompletePair(secret, bytesFilled(0x33), "phone-b", home)
	if err == nil || !strings.Contains(err.Error(), "reused") {
		t.Fatalf("expected reused, got %v", err)
	}
	n, _ := ActiveDeviceCount(home)
	if n != 1 {
		t.Fatalf("active=%d", n)
	}
}

func TestFourthDeviceRefused(t *testing.T) {
	home := pairHome(t)
	for i := 0; i < 3; i++ {
		qr := mustQR(t, home)
		secret, err := ParsePairSecret(qr)
		if err != nil {
			t.Fatal(err)
		}
		if _, err := CompletePair(secret, bytesFilled(byte(i+1)), "phone-"+string(rune('0'+i)), home); err != nil {
			t.Fatal(err)
		}
	}
	n, _ := ActiveDeviceCount(home)
	if n != 3 {
		t.Fatalf("active=%d", n)
	}
	qr := mustQR(t, home)
	secret, _ := ParsePairSecret(qr)
	_, err := CompletePair(secret, bytesFilled(0x44), "phone-3", home)
	if err == nil || !strings.Contains(err.Error(), "device limit") {
		t.Fatalf("expected device limit, got %v", err)
	}
	list, err := ListDevices(home, false)
	if err != nil {
		t.Fatal(err)
	}
	names := map[string]bool{}
	for _, d := range list {
		names[d.Name] = true
	}
	for _, want := range []string{"phone-0", "phone-1", "phone-2"} {
		if !names[want] {
			t.Fatalf("missing %s in %#v", want, names)
		}
	}
}

func TestPairEnvelopeSecretMayContainNUL(t *testing.T) {
	secret := append([]byte{0x00}, append(bytesFilled(0x41)[:30], 0x00)...)
	payload := PairPayload(secret, "phone")
	kind, fields, err := ParseHandshakePayload(payload)
	if err != nil {
		t.Fatal(err)
	}
	if kind != "pair" {
		t.Fatalf("kind=%s", kind)
	}
	if string(fields.Secret) != string(secret) {
		t.Fatal("secret round-trip failed")
	}
	if fields.Name != "phone" {
		t.Fatalf("name=%q", fields.Name)
	}
}

func TestRaw32ByteHandshakePayloadPairs(t *testing.T) {
	home := pairHome(t)
	qr := mustQR(t, home)
	secret, err := ParsePairSecret(qr)
	if err != nil {
		t.Fatal(err)
	}
	kind, fields, err := ParseHandshakePayload(secret)
	if err != nil || kind != "pair" {
		t.Fatalf("kind=%s err=%v", kind, err)
	}
	if string(fields.Secret) != string(secret) {
		t.Fatal("secret mismatch")
	}
	id, err := CompletePair(fields.Secret, bytesFilled(0x77), fields.Name, home)
	if err != nil || id == "" {
		t.Fatalf("id=%q err=%v", id, err)
	}
	n, _ := ActiveDeviceCount(home)
	if n != 1 {
		t.Fatalf("active=%d", n)
	}
}

func TestPairPayloadEnvelopeStillPairs(t *testing.T) {
	home := pairHome(t)
	qr := mustQR(t, home)
	secret, _ := ParsePairSecret(qr)
	kind, fields, err := ParseHandshakePayload(PairPayload(secret, "desk-phone"))
	if err != nil || kind != "pair" || fields.Name != "desk-phone" {
		t.Fatalf("kind=%s name=%q err=%v", kind, fields.Name, err)
	}
	if _, err := CompletePair(fields.Secret, bytesFilled(0x88), fields.Name, home); err != nil {
		t.Fatal(err)
	}
}

func TestPairSecretNotInAudit(t *testing.T) {
	home := pairHome(t)
	qr := mustQR(t, home)
	secret, _ := ParsePairSecret(qr)
	var b64 string
	for _, ln := range strings.Split(qr, "\n") {
		if strings.HasPrefix(ln, "ps=") {
			b64 = ln[3:]
		}
	}
	if _, err := CompletePair(secret, bytesFilled(0x55), "kitchen-phone", home); err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(home, "auth", "connect", "audit.log")
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	text := string(raw)
	if !strings.Contains(text, "pair") || !strings.Contains(text, "kitchen-phone") {
		t.Fatalf("audit missing pair event: %s", text)
	}
	if strings.Contains(text, hex.EncodeToString(secret)) || strings.Contains(text, b64) {
		t.Fatal("audit leaked pair secret")
	}
	if strings.Contains(text, "local_api_token") || strings.Contains(text, "Bearer") {
		t.Fatal("audit leaked auth material")
	}
}

func TestStartPairEmitsTailscaleLine(t *testing.T) {
	home := pairHome(t)
	qr, err := StartPair(PairStartOpts{
		Loopback:  true,
		BindHost:  "192.168.0.46",
		BindPort:  7401,
		Tailscale: "100.64.1.2",
		Home:      home,
	})
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(qr, "ts=100.64.1.2:7401") {
		t.Fatalf("missing ts line:\n%s", qr)
	}
}

func TestStartPairOmitsBadTailscale(t *testing.T) {
	home := pairHome(t)
	for _, ts := range []string{"0.0.0.0", "not-an-ip"} {
		qr, err := StartPair(PairStartOpts{
			Loopback:  true,
			BindHost:  "192.168.0.46",
			BindPort:  7401,
			Tailscale: ts,
			Home:      home,
		})
		if err != nil {
			t.Fatal(err)
		}
		if strings.Contains(qr, "ts=") {
			t.Fatalf("ts must be omitted for %q:\n%s", ts, qr)
		}
	}
}

func TestInvalidPairSecretRejected(t *testing.T) {
	home := pairHome(t)
	_ = mustQR(t, home)
	_, err := CompletePair(bytesFilled(0x09), bytesFilled(0x11), "phone", home)
	if err == nil || !strings.Contains(err.Error(), "invalid") {
		t.Fatalf("expected invalid, got %v", err)
	}
}

func TestDeviceIDIsSHA256Prefix(t *testing.T) {
	pub := bytesFilled(0xab)
	want := deviceIDFor(pub)
	if len(want) != 32 {
		t.Fatalf("id len=%d", len(want))
	}
	home := pairHome(t)
	qr := mustQR(t, home)
	secret, _ := ParsePairSecret(qr)
	id, err := CompletePair(secret, pub, "phone", home)
	if err != nil {
		t.Fatal(err)
	}
	if id != want {
		t.Fatalf("id=%s want=%s", id, want)
	}
}

func bytesFilled(b byte) []byte {
	out := make([]byte, 32)
	for i := range out {
		out[i] = b
	}
	return out
}
