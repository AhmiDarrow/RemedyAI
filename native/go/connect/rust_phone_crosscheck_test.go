package connect_test

import (
	"bytes"
	"encoding/hex"
	"testing"

	"github.com/AhmiDarrow/RemedyAI/native/go/connect"
)

func hx(t *testing.T, s string) []byte {
	t.Helper()
	b, err := hex.DecodeString(s)
	if err != nil {
		t.Fatal(err)
	}
	return b
}

// TestRustPhoneCrossCheck replays bytes produced by the Rust phone core
// (connect/core, `cargo test --lib go_crosscheck -- --nocapture`) through
// this responder: the pairing handshake, records before and after a rekey in
// both directions, and a tunnelled request. It is the proof the two ends
// agree on the wire without running a phone.
func TestRustPhoneCrossCheck(t *testing.T) {
	msg0 := "7b0d47d93427f8311160781c7c733fd89f88970aef490d8aa0ee19a4cb8a1b1461d2949213e755bc180b5d5dbbd8c50a2a1beb69f8940d24cab4606a34cd5d41fecaff8cc7e5d29fa29ca11651e116ca29922e732f1afaeda97d662f5d6e35326cc5b371dd4221410ff9c46478aa56a953ae26ef991b51a4254e4b06c1073ecd0c7f328dd04017b383b1409f63"
	msg1 := "ff2ee45601ec1b67310c7790404585ae697331eee1c1f8cf2419731c1fff3e6bda60bb0d8d67be504336a9cee1ec0203"
	r := map[string]string{
		"r1": "00000027000000000000000000000000e4cba49b18f45b7740c2f006f383491bda0c294e566d50891cf656",
		"r2": "000000270000000001000000000000002e75f7459fc6799f9f0ffdbc1515e762e69bdc5d001053a082b65e",
		"r3": "00000027000000000000000000000000755118c760839a640dfb2c836fc18bcde090d0d8f77de7f5f123e4",
		"r4": "00000047000000000100000000000000fecb703e52c18b36f8c0e943e0654a58b710385fa15071a94b0fb78127e1a2a59eaca83f6a94b71405f57a2ad29182576354013cc050d1be0f28d7",
		"h1": "0000002700000000000000000000000065ad8a28da7d3865859af6fe463a63be11e57a2c4fc41aa43a1ad1",
		"h2": "00000027000000000100000000000000cd22ffaa1c2441dc587a6b122dd1a2124449661353cda8e09fcfe1",
		"h3": "0000002700000000000000000000000088ce7039e017c91d8e2266fb5a36ff137dbfef87f47d27db69d97f",
	}
	host, err := connect.KeyPairFromPrivate(bytes.Repeat([]byte{0x22}, 32))
	if err != nil {
		t.Fatal(err)
	}
	hs, err := connect.NewHandshakeState(connect.HandshakeConfig{Initiator: false, Local: host})
	if err != nil {
		t.Fatal(err)
	}
	if err := hs.SetEphemeralForTest(bytes.Repeat([]byte{0x44}, 32)); err != nil {
		t.Fatal(err)
	}
	payload, err := hs.ReadMessage(hx(t, msg0))
	if err != nil {
		t.Fatal(err)
	}
	kind, f, err := connect.ParseHandshakePayload(payload)
	if err != nil || kind != "pair" || f.Name != "Pixel 9" || !bytes.Equal(f.Secret, bytes.Repeat([]byte{7}, 32)) {
		t.Fatalf("%v %v %+v", err, kind, f)
	}
	out, err := hs.WriteMessage(nil)
	if err != nil || hex.EncodeToString(out) != msg1 {
		t.Fatalf("msg1 mismatch: %v", err)
	}
	send, recv, err := hs.Split()
	if err != nil {
		t.Fatal(err)
	}
	dec := func(k string) connect.InnerFrame {
		pt, err := connect.DecryptRecord(recv, hx(t, r[k]))
		if err != nil {
			t.Fatalf("%s: %v", k, err)
		}
		fr, err := connect.DecodeInner(pt)
		if err != nil {
			t.Fatal(err)
		}
		return fr
	}
	if fr := dec("r1"); fr.Type != connect.TypePing || fr.ID != 1 {
		t.Fatal("r1")
	}
	if fr := dec("r2"); fr.Type != connect.TypeRekey {
		t.Fatal("r2")
	}
	if err := recv.Rekey(); err != nil {
		t.Fatal(err)
	}
	if fr := dec("r3"); fr.Type != connect.TypePing || fr.ID != 2 {
		t.Fatal("r3")
	}
	fr := dec("r4")
	req, err := connect.DecodeHTTPRequest(fr.Payload)
	if err != nil || fr.Type != connect.TypeHTTPReq || fr.ID != 3 || req.Method != "GET" || req.Target != "/api/ping" || req.Headers != "Accept: */*" {
		t.Fatalf("r4 %+v %v", req, err)
	}
	enc := func(typ uint8, id uint32) string {
		f, _ := connect.EncodeInner(typ, id, nil, true)
		b, err := connect.EncryptRecord(send, f)
		if err != nil {
			t.Fatal(err)
		}
		return hex.EncodeToString(b)
	}
	if enc(connect.TypePong, 1) != r["h1"] {
		t.Fatal("h1")
	}
	if enc(connect.TypeRekey, 0) != r["h2"] {
		t.Fatal("h2")
	}
	if err := send.Rekey(); err != nil {
		t.Fatal(err)
	}
	if enc(connect.TypePong, 2) != r["h3"] {
		t.Fatal("h3")
	}
}
