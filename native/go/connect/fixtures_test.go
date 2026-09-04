package connect_test

import (
	"encoding/hex"
	"encoding/json"
	"os"
	"path/filepath"
	"testing"

	"github.com/AhmiDarrow/RemedyAI/native/go/connect"
)

func fixtureDir(t *testing.T) string {
	t.Helper()
	// native/go/connect → repo root → tests/fixtures/connect
	dir, err := filepath.Abs(filepath.Join("..", "..", "..", "tests", "fixtures", "connect"))
	if err != nil {
		t.Fatal(err)
	}
	if st, err := os.Stat(dir); err != nil || !st.IsDir() {
		t.Fatalf("missing fixture dir %s (run from native/go or package dir)", dir)
	}
	return dir
}

func loadJSON(t *testing.T, name string, dest any) {
	t.Helper()
	raw, err := os.ReadFile(filepath.Join(fixtureDir(t), name))
	if err != nil {
		t.Fatalf("missing fixture %s; regenerate via python -m tests.harness.connect_fixture_capture: %v", name, err)
	}
	if err := json.Unmarshal(raw, dest); err != nil {
		t.Fatalf("%s: %v", name, err)
	}
}

func mustHex(t *testing.T, s string) []byte {
	t.Helper()
	if s == "" {
		return nil
	}
	b, err := hex.DecodeString(s)
	if err != nil {
		t.Fatalf("hex: %v", err)
	}
	return b
}

func TestFixtureFilesPresent(t *testing.T) {
	required := []string{
		"noise_ik_index.json",
		"noise_ik_android_debug_hash.json",
		"noise_ik_snow.json",
		"noise_ik_pair_secret.json",
		"noise_ik_post_split.json",
		"record_framing.json",
	}
	dir := fixtureDir(t)
	for _, name := range required {
		st, err := os.Stat(filepath.Join(dir, name))
		if err != nil || st.Size() < 40 {
			t.Fatalf("%s missing or too small", name)
		}
	}
}

func TestIndexRecordsAndroidMatch(t *testing.T) {
	var idx struct {
		ProtocolNameUTF8        string   `json:"protocol_name_utf8"`
		ProductPrologueUTF8     string   `json:"product_prologue_utf8"`
		AndroidHexMatchedPython bool     `json:"android_hex_matched_python"`
		Vectors                 []string `json:"vectors"`
	}
	loadJSON(t, "noise_ik_index.json", &idx)
	if idx.ProtocolNameUTF8 != connect.ProtocolName {
		t.Fatalf("protocol = %q", idx.ProtocolNameUTF8)
	}
	if idx.ProductPrologueUTF8 != connect.Prologue {
		t.Fatalf("prologue = %q", idx.ProductPrologueUTF8)
	}
	if !idx.AndroidHexMatchedPython {
		t.Fatal("android_hex_matched_python is false")
	}
	want := map[string]bool{
		"snow_ik_chacha_blake2s": false,
		"remedy_pair_secret":     false,
		"remedy_post_split_aead": false,
	}
	for _, v := range idx.Vectors {
		if _, ok := want[v]; ok {
			want[v] = true
		}
	}
	for name, ok := range want {
		if !ok {
			t.Fatalf("index missing vector %s", name)
		}
	}
}

func TestAndroidDebugHashMatchesGo(t *testing.T) {
	var fx struct {
		AndroidHexMatchedPython bool   `json:"android_hex_matched_python"`
		H0Hex                   string `json:"h0_hex"`
		HPrologueHex            string `json:"h_prologue_hex"`
		HRsHex                  string `json:"h_rs_hex"`
		HEHex                   string `json:"h_e_hex"`
		KAfterEsHex             string `json:"k_after_es_hex"`
		EncSHex                 string `json:"enc_s_hex"`
	}
	loadJSON(t, "noise_ik_android_debug_hash.json", &fx)
	if !fx.AndroidHexMatchedPython {
		t.Fatal("fixture not android-matched")
	}

	h0 := connect.HashBLAKE2s([]byte(connect.ProtocolName))
	assertHex(t, "h0", h0, fx.H0Hex)

	prologue := mustHex(t, "5468657265206973206e6f20726967687420616e642077726f6e672e205468"+
		"6572652773206f6e6c792066756e20616e6420626f72696e672e")
	h1 := connect.HashBLAKE2s(append(append([]byte{}, h0...), prologue...))
	assertHex(t, "h_prologue", h1, fx.HPrologueHex)

	initRs := mustHex(t, "ea82fd2e81d1285f1b2029e46ca7bcaeeeafed15396d002bd434624a4d580655")
	h2 := connect.HashBLAKE2s(append(append([]byte{}, h1...), initRs...))
	assertHex(t, "h_rs", h2, fx.HRsHex)

	initEph, err := connect.KeyPairFromPrivate(mustHex(t, "cc95b4ccc4912c5a52c8d2f6b808e13712392c4468f4e3f02a7d2d1590cb9178"))
	if err != nil {
		t.Fatal(err)
	}
	h3 := connect.HashBLAKE2s(append(append([]byte{}, h2...), initEph.Public...))
	assertHex(t, "h_e", h3, fx.HEHex)

	shared, err := connect.DH(initEph, initRs)
	if err != nil {
		t.Fatal(err)
	}
	_, k, err := hkdf2(t, h0, shared)
	if err != nil {
		t.Fatal(err)
	}
	assertHex(t, "k_after_es", k, fx.KAfterEsHex)

	initStatic, err := connect.KeyPairFromPrivate(mustHex(t, "b7e117ce8ede06ceb89500799a3778d097fc54a3f90bea744493dfc24ec21f32"))
	if err != nil {
		t.Fatal(err)
	}
	encS, err := connect.AEADEncrypt(k, 0, h3, initStatic.Public)
	if err != nil {
		t.Fatal(err)
	}
	assertHex(t, "enc_s", encS, fx.EncSHex)
}

func TestSnowIKFixtureMatchesAndroid(t *testing.T) {
	var fx struct {
		Name                    string `json:"name"`
		AndroidHexMatchedPython bool   `json:"android_hex_matched_python"`
		InitStaticPrivHex       string `json:"init_static_priv_hex"`
		InitStaticPubHex        string `json:"init_static_pub_hex"`
		InitEphPrivHex          string `json:"init_eph_priv_hex"`
		RespStaticPrivHex       string `json:"resp_static_priv_hex"`
		RespStaticPubHex        string `json:"resp_static_pub_hex"`
		RespEphPrivHex          string `json:"resp_eph_priv_hex"`
		PrologueHex             string `json:"prologue_hex"`
		Msg0PayloadHex          string `json:"msg0_payload_hex"`
		Msg0CiphertextHex       string `json:"msg0_ciphertext_hex"`
		Msg1PayloadHex          string `json:"msg1_payload_hex"`
		Msg1CiphertextHex       string `json:"msg1_ciphertext_hex"`
		Msg2PayloadHex          string `json:"msg2_payload_hex"`
		Msg2CiphertextHex       string `json:"msg2_ciphertext_hex"`
		Msg3PayloadHex          string `json:"msg3_payload_hex"`
		Msg3CiphertextHex       string `json:"msg3_ciphertext_hex"`
		AndroidExpected         struct {
			Msg0CiphertextHex string `json:"msg0_ciphertext_hex"`
			Msg1CiphertextHex string `json:"msg1_ciphertext_hex"`
			Msg2CiphertextHex string `json:"msg2_ciphertext_hex"`
			Msg3CiphertextHex string `json:"msg3_ciphertext_hex"`
		} `json:"android_expected"`
	}
	loadJSON(t, "noise_ik_snow.json", &fx)
	if fx.Name != "snow_ik_chacha_blake2s" || !fx.AndroidHexMatchedPython {
		t.Fatalf("unexpected fixture meta name=%s matched=%v", fx.Name, fx.AndroidHexMatchedPython)
	}

	phone, err := connect.KeyPairFromPrivate(mustHex(t, fx.InitStaticPrivHex))
	if err != nil {
		t.Fatal(err)
	}
	host, err := connect.KeyPairFromPrivate(mustHex(t, fx.RespStaticPrivHex))
	if err != nil {
		t.Fatal(err)
	}
	assertHex(t, "host pub", host.Public, fx.RespStaticPubHex)
	assertHex(t, "phone pub", phone.Public, fx.InitStaticPubHex)

	prologue := mustHex(t, fx.PrologueHex)
	init, err := connect.NewHandshakeState(connect.HandshakeConfig{
		Initiator:    true,
		Local:        phone,
		RemoteStatic: host.Public,
		Prologue:     prologue,
	})
	if err != nil {
		t.Fatal(err)
	}
	resp, err := connect.NewHandshakeState(connect.HandshakeConfig{
		Initiator: false,
		Local:     host,
		Prologue:  prologue,
	})
	if err != nil {
		t.Fatal(err)
	}
	if err := init.SetEphemeralForTest(mustHex(t, fx.InitEphPrivHex)); err != nil {
		t.Fatal(err)
	}
	if err := resp.SetEphemeralForTest(mustHex(t, fx.RespEphPrivHex)); err != nil {
		t.Fatal(err)
	}

	msg0, err := init.WriteMessage(mustHex(t, fx.Msg0PayloadHex))
	if err != nil {
		t.Fatal(err)
	}
	assertHex(t, "msg0", msg0, fx.Msg0CiphertextHex)
	assertHex(t, "msg0 android", msg0, fx.AndroidExpected.Msg0CiphertextHex)
	got0, err := resp.ReadMessage(msg0)
	if err != nil {
		t.Fatal(err)
	}
	assertHex(t, "msg0 payload", got0, fx.Msg0PayloadHex)

	msg1, err := resp.WriteMessage(mustHex(t, fx.Msg1PayloadHex))
	if err != nil {
		t.Fatal(err)
	}
	assertHex(t, "msg1", msg1, fx.Msg1CiphertextHex)
	assertHex(t, "msg1 android", msg1, fx.AndroidExpected.Msg1CiphertextHex)
	got1, err := init.ReadMessage(msg1)
	if err != nil {
		t.Fatal(err)
	}
	assertHex(t, "msg1 payload", got1, fx.Msg1PayloadHex)

	sendI, recvI, err := init.Split()
	if err != nil {
		t.Fatal(err)
	}
	sendR, recvR, err := resp.Split()
	if err != nil {
		t.Fatal(err)
	}

	msg2, err := sendI.EncryptWithAd(nil, mustHex(t, fx.Msg2PayloadHex))
	if err != nil {
		t.Fatal(err)
	}
	assertHex(t, "msg2", msg2, fx.Msg2CiphertextHex)
	assertHex(t, "msg2 android", msg2, fx.AndroidExpected.Msg2CiphertextHex)
	pt2, err := recvR.DecryptWithAd(nil, msg2)
	if err != nil {
		t.Fatal(err)
	}
	assertHex(t, "msg2 payload", pt2, fx.Msg2PayloadHex)

	msg3, err := sendR.EncryptWithAd(nil, mustHex(t, fx.Msg3PayloadHex))
	if err != nil {
		t.Fatal(err)
	}
	assertHex(t, "msg3", msg3, fx.Msg3CiphertextHex)
	assertHex(t, "msg3 android", msg3, fx.AndroidExpected.Msg3CiphertextHex)
	pt3, err := recvI.DecryptWithAd(nil, msg3)
	if err != nil {
		t.Fatal(err)
	}
	assertHex(t, "msg3 payload", pt3, fx.Msg3PayloadHex)
}

func TestPairSecretFirstPayloadSuccess(t *testing.T) {
	var fx struct {
		Name               string `json:"name"`
		PairSecretHex      string `json:"pair_secret_hex"`
		PairSecretVerifyOK bool   `json:"pair_secret_verify_ok"`
		WrongPSVerifyOK    bool   `json:"wrong_ps_verify_ok"`
		InitStaticPrivHex  string `json:"init_static_priv_hex"`
		InitEphPrivHex     string `json:"init_eph_priv_hex"`
		RespStaticPrivHex  string `json:"resp_static_priv_hex"`
		RespEphPrivHex     string `json:"resp_eph_priv_hex"`
		PrologueHex        string `json:"prologue_hex"`
		Msg0CiphertextHex  string `json:"msg0_ciphertext_hex"`
		Msg1CiphertextHex  string `json:"msg1_ciphertext_hex"`
	}
	loadJSON(t, "noise_ik_pair_secret.json", &fx)
	if fx.Name != "remedy_pair_secret" {
		t.Fatalf("name=%s", fx.Name)
	}
	if string(mustHex(t, fx.PrologueHex)) != connect.Prologue {
		t.Fatal("prologue mismatch")
	}
	ps := mustHex(t, fx.PairSecretHex)
	if len(ps) != connect.DHLen || !fx.PairSecretVerifyOK || fx.WrongPSVerifyOK {
		t.Fatalf("pair secret meta invalid")
	}

	phone, host := loadPair(t, fx.InitStaticPrivHex, fx.RespStaticPrivHex)
	init, resp := newProductPair(t, phone, host)
	mustEph(t, init, fx.InitEphPrivHex)
	mustEph(t, resp, fx.RespEphPrivHex)

	msg0, err := init.WriteMessage(ps)
	if err != nil {
		t.Fatal(err)
	}
	assertHex(t, "msg0", msg0, fx.Msg0CiphertextHex)
	got, err := resp.ReadMessage(msg0)
	if err != nil {
		t.Fatal(err)
	}
	if !connect.VerifyPairSecret(got, ps) {
		t.Fatal("pair secret verify failed")
	}

	msg1, err := resp.WriteMessage(nil)
	if err != nil {
		t.Fatal(err)
	}
	assertHex(t, "msg1", msg1, fx.Msg1CiphertextHex)
	got1, err := init.ReadMessage(msg1)
	if err != nil {
		t.Fatal(err)
	}
	if len(got1) != 0 {
		t.Fatalf("msg1 payload len=%d", len(got1))
	}
}

func TestWrongPSFailsClosedAfterDecrypt(t *testing.T) {
	var fx struct {
		PairSecretHex      string `json:"pair_secret_hex"`
		WrongPairSecretHex string `json:"wrong_pair_secret_hex"`
		InitStaticPrivHex  string `json:"init_static_priv_hex"`
		InitEphPrivHex     string `json:"init_eph_priv_hex"`
		RespStaticPrivHex  string `json:"resp_static_priv_hex"`
		RespEphPrivHex     string `json:"resp_eph_priv_hex"`
	}
	loadJSON(t, "noise_ik_pair_secret.json", &fx)
	ps := mustHex(t, fx.PairSecretHex)
	wrong := mustHex(t, fx.WrongPairSecretHex)
	if connect.VerifyPairSecret(ps, wrong) {
		t.Fatal("ps must not equal wrong")
	}

	phone, host := loadPair(t, fx.InitStaticPrivHex, fx.RespStaticPrivHex)
	init, resp := newProductPair(t, phone, host)
	mustEph(t, init, fx.InitEphPrivHex)
	mustEph(t, resp, fx.RespEphPrivHex)

	msg0, err := init.WriteMessage(ps)
	if err != nil {
		t.Fatal(err)
	}
	got, err := resp.ReadMessage(msg0)
	if err != nil {
		t.Fatal(err)
	}
	if len(got) != connect.DHLen {
		t.Fatalf("got len=%d", len(got))
	}
	if connect.VerifyPairSecret(got, wrong) {
		t.Fatal("wrong PS must fail closed")
	}
	if !connect.VerifyPairSecret(got, ps) {
		t.Fatal("correct PS must verify")
	}
}

func TestPostSplitEncryptDecryptBothDirections(t *testing.T) {
	var fx struct {
		Name                       string `json:"name"`
		InitStaticPrivHex          string `json:"init_static_priv_hex"`
		InitEphPrivHex             string `json:"init_eph_priv_hex"`
		RespStaticPrivHex          string `json:"resp_static_priv_hex"`
		RespEphPrivHex             string `json:"resp_eph_priv_hex"`
		HandshakeMsg0PayloadHex    string `json:"handshake_msg0_payload_hex"`
		HandshakeMsg0CiphertextHex string `json:"handshake_msg0_ciphertext_hex"`
		HandshakeMsg1CiphertextHex string `json:"handshake_msg1_ciphertext_hex"`
		TransportIToRPlaintextHex  string `json:"transport_i_to_r_plaintext_hex"`
		TransportIToRCiphertextHex string `json:"transport_i_to_r_ciphertext_hex"`
		TransportRToIPlaintextHex  string `json:"transport_r_to_i_plaintext_hex"`
		TransportRToICiphertextHex string `json:"transport_r_to_i_ciphertext_hex"`
		ADHex                      string `json:"ad_hex"`
	}
	loadJSON(t, "noise_ik_post_split.json", &fx)
	if fx.Name != "remedy_post_split_aead" {
		t.Fatalf("name=%s", fx.Name)
	}

	phone, host := loadPair(t, fx.InitStaticPrivHex, fx.RespStaticPrivHex)
	init, resp := newProductPair(t, phone, host)
	mustEph(t, init, fx.InitEphPrivHex)
	mustEph(t, resp, fx.RespEphPrivHex)

	msg0, err := init.WriteMessage(mustHex(t, fx.HandshakeMsg0PayloadHex))
	if err != nil {
		t.Fatal(err)
	}
	assertHex(t, "hs msg0", msg0, fx.HandshakeMsg0CiphertextHex)
	if _, err := resp.ReadMessage(msg0); err != nil {
		t.Fatal(err)
	}
	msg1, err := resp.WriteMessage(nil)
	if err != nil {
		t.Fatal(err)
	}
	assertHex(t, "hs msg1", msg1, fx.HandshakeMsg1CiphertextHex)
	if _, err := init.ReadMessage(msg1); err != nil {
		t.Fatal(err)
	}

	sendI, recvI, err := init.Split()
	if err != nil {
		t.Fatal(err)
	}
	sendR, recvR, err := resp.Split()
	if err != nil {
		t.Fatal(err)
	}
	ad := mustHex(t, fx.ADHex)

	ctI, err := sendI.EncryptWithAd(ad, mustHex(t, fx.TransportIToRPlaintextHex))
	if err != nil {
		t.Fatal(err)
	}
	assertHex(t, "i→r", ctI, fx.TransportIToRCiphertextHex)
	ptI, err := recvR.DecryptWithAd(ad, ctI)
	if err != nil {
		t.Fatal(err)
	}
	assertHex(t, "i→r pt", ptI, fx.TransportIToRPlaintextHex)

	ctR, err := sendR.EncryptWithAd(ad, mustHex(t, fx.TransportRToIPlaintextHex))
	if err != nil {
		t.Fatal(err)
	}
	assertHex(t, "r→i", ctR, fx.TransportRToICiphertextHex)
	ptR, err := recvI.DecryptWithAd(ad, ctR)
	if err != nil {
		t.Fatal(err)
	}
	assertHex(t, "r→i pt", ptR, fx.TransportRToIPlaintextHex)
}

func assertHex(t *testing.T, label string, got []byte, wantHex string) {
	t.Helper()
	want := mustHex(t, wantHex)
	if hex.EncodeToString(got) != hex.EncodeToString(want) {
		t.Fatalf("%s:\n got %x\nwant %x", label, got, want)
	}
}

func hkdf2(t *testing.T, ck, ikm []byte) (ckOut, k []byte, err error) {
	t.Helper()
	out, err := connect.HKDF(ck, ikm, 2)
	if err != nil {
		return nil, nil, err
	}
	return out[0], out[1], nil
}

func loadPair(t *testing.T, initPrivHex, respPrivHex string) (phone, host connect.KeyPair) {
	t.Helper()
	var err error
	phone, err = connect.KeyPairFromPrivate(mustHex(t, initPrivHex))
	if err != nil {
		t.Fatal(err)
	}
	host, err = connect.KeyPairFromPrivate(mustHex(t, respPrivHex))
	if err != nil {
		t.Fatal(err)
	}
	return phone, host
}

func newProductPair(t *testing.T, phone, host connect.KeyPair) (init, resp *connect.HandshakeState) {
	t.Helper()
	var err error
	init, err = connect.NewHandshakeState(connect.HandshakeConfig{
		Initiator:    true,
		Local:        phone,
		RemoteStatic: host.Public,
		Prologue:     []byte(connect.Prologue),
	})
	if err != nil {
		t.Fatal(err)
	}
	resp, err = connect.NewHandshakeState(connect.HandshakeConfig{
		Initiator: false,
		Local:     host,
		Prologue:  []byte(connect.Prologue),
	})
	if err != nil {
		t.Fatal(err)
	}
	return init, resp
}

func mustEph(t *testing.T, hs *connect.HandshakeState, privHex string) {
	t.Helper()
	if err := hs.SetEphemeralForTest(mustHex(t, privHex)); err != nil {
		t.Fatal(err)
	}
}
