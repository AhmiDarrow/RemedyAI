package connect_test

import (
	"bytes"
	"encoding/binary"
	"encoding/hex"
	"errors"
	"io"
	"testing"

	"github.com/AhmiDarrow/RemedyAI/native/go/connect"
)

type recordEncryptVec struct {
	PlaintextHex  string `json:"plaintext_hex"`
	NonceHex      string `json:"nonce_hex"`
	CiphertextHex string `json:"ciphertext_hex"`
	RecordHex     string `json:"record_hex"`
	NonceCounter  uint64 `json:"nonce_counter"`
}

type recordFixture struct {
	Name                    string `json:"name"`
	MaxRecord               int    `json:"max_record"`
	NonceLen                int    `json:"nonce_len"`
	TagLen                  int    `json:"tag_len"`
	MaxPlaintext            int    `json:"max_plaintext"`
	InitStaticPrivHex       string `json:"init_static_priv_hex"`
	InitEphPrivHex          string `json:"init_eph_priv_hex"`
	RespStaticPrivHex       string `json:"resp_static_priv_hex"`
	RespEphPrivHex          string `json:"resp_eph_priv_hex"`
	HandshakeMsg0PayloadHex string `json:"handshake_msg0_payload_hex"`
	PackVectors             []struct {
		NonceHex      string `json:"nonce_hex"`
		CiphertextHex string `json:"ciphertext_hex"`
		RecordHex     string `json:"record_hex"`
		Length        int    `json:"length"`
	} `json:"pack_vectors"`
	EncryptIToR []recordEncryptVec `json:"encrypt_i_to_r"`
	EncryptRToI []recordEncryptVec `json:"encrypt_r_to_i"`
	Reject      struct {
		OversizeLengthFieldHex string `json:"oversize_length_field_hex"`
		TooShortLengthHex      string `json:"too_short_length_hex"`
		TruncatedClaim12Hex    string `json:"truncated_claim12_hex"`
	} `json:"reject"`
}

func loadRecordFixture(t *testing.T) recordFixture {
	t.Helper()
	var fx recordFixture
	loadJSON(t, "record_framing.json", &fx)
	if fx.Name != "remedy_record_framing" {
		t.Fatalf("name=%s", fx.Name)
	}
	return fx
}

func TestRecordFixtureConstants(t *testing.T) {
	fx := loadRecordFixture(t)
	if fx.MaxRecord != connect.MaxRecord || connect.MaxRecord != 65536 {
		t.Fatalf("MaxRecord=%d fixture=%d", connect.MaxRecord, fx.MaxRecord)
	}
	if fx.NonceLen != connect.NonceLen {
		t.Fatalf("NonceLen=%d", connect.NonceLen)
	}
	if fx.TagLen != connect.TagLen {
		t.Fatalf("TagLen=%d", connect.TagLen)
	}
	if fx.MaxPlaintext != connect.MaxPlaintext {
		t.Fatalf("MaxPlaintext=%d fixture=%d", connect.MaxPlaintext, fx.MaxPlaintext)
	}
}

func TestPackUnpackFixtureVectors(t *testing.T) {
	fx := loadRecordFixture(t)
	for i, v := range fx.PackVectors {
		nonce := mustHex(t, v.NonceHex)
		ct := mustHex(t, v.CiphertextHex)
		blob, err := connect.PackRecord(nonce, ct)
		if err != nil {
			t.Fatalf("pack[%d]: %v", i, err)
		}
		assertHex(t, "pack", blob, v.RecordHex)
		if len(blob) < 4 || int(binary.BigEndian.Uint32(blob[:4])) != v.Length {
			t.Fatalf("length field[%d]=%d want %d", i, binary.BigEndian.Uint32(blob[:4]), v.Length)
		}
		gotN, gotCT, err := connect.UnpackRecord(blob)
		if err != nil {
			t.Fatalf("unpack[%d]: %v", i, err)
		}
		assertHex(t, "nonce", gotN, v.NonceHex)
		assertHex(t, "ct", gotCT, v.CiphertextHex)
	}
}

func TestPackRejectsBadNonceLength(t *testing.T) {
	for _, n := range []int{0, 11, 13, 16} {
		_, err := connect.PackRecord(make([]byte, n), []byte("ct"))
		if !errors.Is(err, connect.ErrRecord) {
			t.Fatalf("nonce len %d: %v", n, err)
		}
	}
}

func TestUnpackRejectFamilyFromFixture(t *testing.T) {
	fx := loadRecordFixture(t)
	for _, tc := range []struct {
		label string
		hex   string
	}{
		{"oversize", fx.Reject.OversizeLengthFieldHex},
		{"too_short", fx.Reject.TooShortLengthHex},
		{"truncated_claim12", fx.Reject.TruncatedClaim12Hex},
	} {
		blob := mustHex(t, tc.hex)
		_, _, err := connect.UnpackRecord(blob)
		if !errors.Is(err, connect.ErrRecord) {
			t.Fatalf("%s: %v", tc.label, err)
		}
	}
	for _, blob := range [][]byte{
		{},
		{0, 0},
		append(mustHex(t, "0000000c"), make([]byte, 11)...),
		append(mustHex(t, "0000000d"), make([]byte, 12)...),
	} {
		_, _, err := connect.UnpackRecord(blob)
		if !errors.Is(err, connect.ErrRecord) {
			t.Fatalf("truncated family: %v", err)
		}
	}
}

func TestRecordAtMaxAccepted(t *testing.T) {
	ct := make([]byte, connect.MaxRecord-connect.NonceLen)
	blob, err := connect.PackRecord(make([]byte, connect.NonceLen), ct)
	if err != nil {
		t.Fatal(err)
	}
	nonce, got, err := connect.UnpackRecord(blob)
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(nonce, make([]byte, connect.NonceLen)) || !bytes.Equal(got, ct) {
		t.Fatal("max record roundtrip mismatch")
	}
}

func TestRecordOverMaxRejected(t *testing.T) {
	for _, oversize := range []int{connect.MaxRecord + 1, connect.MaxRecord + 16, connect.MaxRecord + 65535} {
		ct := make([]byte, oversize-connect.NonceLen)
		if _, err := connect.PackRecord(make([]byte, connect.NonceLen), ct); !errors.Is(err, connect.ErrRecord) {
			t.Fatalf("pack oversize %d: %v", oversize, err)
		}
		hdr := make([]byte, 4)
		binary.BigEndian.PutUint32(hdr, uint32(oversize))
		if _, _, err := connect.UnpackRecord(append(hdr, make([]byte, 20)...)); !errors.Is(err, connect.ErrRecord) {
			t.Fatalf("unpack oversize %d: %v", oversize, err)
		}
	}
}

func splitProductTransport(t *testing.T, fx recordFixture) (sendI, recvR, sendR, recvI *connect.CipherState) {
	t.Helper()
	phone, host := loadPair(t, fx.InitStaticPrivHex, fx.RespStaticPrivHex)
	init, resp := newProductPair(t, phone, host)
	mustEph(t, init, fx.InitEphPrivHex)
	mustEph(t, resp, fx.RespEphPrivHex)
	msg0, err := init.WriteMessage(mustHex(t, fx.HandshakeMsg0PayloadHex))
	if err != nil {
		t.Fatal(err)
	}
	if _, err := resp.ReadMessage(msg0); err != nil {
		t.Fatal(err)
	}
	msg1, err := resp.WriteMessage(nil)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := init.ReadMessage(msg1); err != nil {
		t.Fatal(err)
	}
	sendI, recvI, err = init.Split()
	if err != nil {
		t.Fatal(err)
	}
	sendR, recvR, err = resp.Split()
	if err != nil {
		t.Fatal(err)
	}
	return sendI, recvR, sendR, recvI
}

func TestEncryptRecordFixtureIToR(t *testing.T) {
	fx := loadRecordFixture(t)
	sendI, recvR, _, _ := splitProductTransport(t, fx)
	for i, v := range fx.EncryptIToR {
		if sendI.Nonce() != v.NonceCounter {
			t.Fatalf("[%d] nonce counter=%d want %d", i, sendI.Nonce(), v.NonceCounter)
		}
		blob, err := connect.EncryptRecord(sendI, mustHex(t, v.PlaintextHex))
		if err != nil {
			t.Fatalf("encrypt[%d]: %v", i, err)
		}
		assertHex(t, "record", blob, v.RecordHex)
		nonce, ct, err := connect.UnpackRecord(blob)
		if err != nil {
			t.Fatal(err)
		}
		assertHex(t, "nonce", nonce, v.NonceHex)
		assertHex(t, "ct", ct, v.CiphertextHex)
		pt, err := connect.DecryptRecord(recvR, blob)
		if err != nil {
			t.Fatalf("decrypt[%d]: %v", i, err)
		}
		assertHex(t, "pt", pt, v.PlaintextHex)
	}
}

func TestEncryptRecordFixtureRToI(t *testing.T) {
	fx := loadRecordFixture(t)
	_, _, sendR, recvI := splitProductTransport(t, fx)
	for i, v := range fx.EncryptRToI {
		blob, err := connect.EncryptRecord(sendR, mustHex(t, v.PlaintextHex))
		if err != nil {
			t.Fatalf("encrypt[%d]: %v", i, err)
		}
		assertHex(t, "record", blob, v.RecordHex)
		pt, err := connect.DecryptRecord(recvI, blob)
		if err != nil {
			t.Fatal(err)
		}
		assertHex(t, "pt", pt, v.PlaintextHex)
	}
}

func TestDecryptRecordReplayFails(t *testing.T) {
	fx := loadRecordFixture(t)
	sendI, recvR, _, _ := splitProductTransport(t, fx)
	blob, err := connect.EncryptRecord(sendI, []byte("first"))
	if err != nil {
		t.Fatal(err)
	}
	if _, err := connect.DecryptRecord(recvR, blob); err != nil {
		t.Fatal(err)
	}
	if _, err := connect.DecryptRecord(recvR, blob); !errors.Is(err, connect.ErrNoise) {
		t.Fatalf("replay: %v", err)
	}
}

func TestDecryptRecordRewoundNonceFails(t *testing.T) {
	fx := loadRecordFixture(t)
	sendI, recvR, _, _ := splitProductTransport(t, fx)
	first, err := connect.EncryptRecord(sendI, []byte("a"))
	if err != nil {
		t.Fatal(err)
	}
	second, err := connect.EncryptRecord(sendI, []byte("b"))
	if err != nil {
		t.Fatal(err)
	}
	if _, err := connect.DecryptRecord(recvR, first); err != nil {
		t.Fatal(err)
	}
	if _, err := connect.DecryptRecord(recvR, second); err != nil {
		t.Fatal(err)
	}
	if _, err := connect.DecryptRecord(recvR, first); !errors.Is(err, connect.ErrNoise) {
		t.Fatalf("rewound: %v", err)
	}
}

func TestDecryptRecordFutureNonceFails(t *testing.T) {
	fx := loadRecordFixture(t)
	sendI, recvR, _, _ := splitProductTransport(t, fx)
	blob, err := connect.EncryptRecord(sendI, []byte("x"))
	if err != nil {
		t.Fatal(err)
	}
	_, ct, err := connect.UnpackRecord(blob)
	if err != nil {
		t.Fatal(err)
	}
	future, err := connect.PackRecord(connect.EncodeNonce(5), ct)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := connect.DecryptRecord(recvR, future); !errors.Is(err, connect.ErrNoise) {
		t.Fatalf("future: %v", err)
	}
}

func TestEncryptRecordOversizeDoesNotConsumeNonce(t *testing.T) {
	fx := loadRecordFixture(t)
	sendI, recvR, _, _ := splitProductTransport(t, fx)
	before := sendI.Nonce()
	if _, err := connect.EncryptRecord(sendI, make([]byte, connect.MaxPlaintext+1)); !errors.Is(err, connect.ErrRecord) {
		t.Fatalf("oversize: %v", err)
	}
	if sendI.Nonce() != before {
		t.Fatalf("nonce advanced on reject")
	}
	blob, err := connect.EncryptRecord(sendI, []byte("ok"))
	if err != nil {
		t.Fatal(err)
	}
	pt, err := connect.DecryptRecord(recvR, blob)
	if err != nil || string(pt) != "ok" {
		t.Fatalf("pt=%q err=%v", pt, err)
	}
}

func TestEncryptRecordMaxPlaintextAccepted(t *testing.T) {
	fx := loadRecordFixture(t)
	sendI, recvR, _, _ := splitProductTransport(t, fx)
	plain := bytes.Repeat([]byte("Z"), connect.MaxPlaintext)
	blob, err := connect.EncryptRecord(sendI, plain)
	if err != nil {
		t.Fatal(err)
	}
	pt, err := connect.DecryptRecord(recvR, blob)
	if err != nil || !bytes.Equal(pt, plain) {
		t.Fatal("max plaintext mismatch")
	}
}

func TestReadRecordRoundtrip(t *testing.T) {
	fx := loadRecordFixture(t)
	v := fx.PackVectors[1]
	blob := mustHex(t, v.RecordHex)
	nonce, ct, err := connect.ReadRecord(bytes.NewReader(blob))
	if err != nil {
		t.Fatal(err)
	}
	assertHex(t, "nonce", nonce, v.NonceHex)
	assertHex(t, "ct", ct, v.CiphertextHex)
}

func TestReadRecordRejectsOversize(t *testing.T) {
	fx := loadRecordFixture(t)
	hdr := mustHex(t, fx.Reject.OversizeLengthFieldHex)
	_, _, err := connect.ReadRecord(bytes.NewReader(hdr))
	if !errors.Is(err, connect.ErrRecord) {
		t.Fatalf("got %v", err)
	}
}

func TestReadRecordTruncatedBody(t *testing.T) {
	hdr := make([]byte, 4)
	binary.BigEndian.PutUint32(hdr, 20)
	r := bytes.NewReader(append(hdr, make([]byte, 4)...))
	_, _, err := connect.ReadRecord(r)
	if !errors.Is(err, io.ErrUnexpectedEOF) && !errors.Is(err, io.EOF) {
		t.Fatalf("got %v", err)
	}
}

func TestEncodeNonceMatchesFixture(t *testing.T) {
	fx := loadRecordFixture(t)
	for _, v := range fx.EncryptIToR {
		got := hex.EncodeToString(connect.EncodeNonce(v.NonceCounter))
		if got != v.NonceHex {
			t.Fatalf("EncodeNonce(%d)=%s want %s", v.NonceCounter, got, v.NonceHex)
		}
	}
}
