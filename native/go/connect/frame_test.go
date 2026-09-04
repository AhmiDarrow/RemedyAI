package connect

import (
	"encoding/binary"
	"encoding/hex"
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
)

func loadInnerFixture(t *testing.T) map[string]any {
	t.Helper()
	path := filepath.Join("..", "..", "..", "tests", "fixtures", "connect", "inner_framing.json")
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var out map[string]any
	if err := json.Unmarshal(raw, &out); err != nil {
		t.Fatal(err)
	}
	return out
}

func mustHex(t *testing.T, s string) []byte {
	t.Helper()
	b, err := hex.DecodeString(s)
	if err != nil {
		t.Fatal(err)
	}
	return b
}

func TestInnerFrameRoundTripAndroidVector(t *testing.T) {
	fix := loadInnerFixture(t)
	want := mustHex(t, fix["android_http_req_id7"].(string))
	reqPayload, err := EncodeHTTPRequest(HTTPRequest{
		Method:  "GET",
		Target:  "/api/sessions?limit=20",
		Headers: "",
		Body:    nil,
	})
	if err != nil {
		t.Fatal(err)
	}
	got, err := EncodeInner(TypeHTTPReq, 7, reqPayload, true)
	if err != nil {
		t.Fatal(err)
	}
	if hex.EncodeToString(got) != hex.EncodeToString(want) {
		t.Fatalf("encode mismatch\n got %x\nwant %x", got, want)
	}
	frame, err := DecodeInner(want)
	if err != nil {
		t.Fatal(err)
	}
	if frame.Type != TypeHTTPReq || frame.ID != 7 || !frame.Fin() {
		t.Fatalf("frame=%+v", frame)
	}
	req, err := DecodeHTTPRequest(frame.Payload)
	if err != nil {
		t.Fatal(err)
	}
	if req.Method != "GET" || req.Target != "/api/sessions?limit=20" {
		t.Fatalf("req=%+v", req)
	}
}

func TestHTTPResponseFixture(t *testing.T) {
	fix := loadInnerFixture(t)
	want := mustHex(t, fix["http_res_payload"].(string))
	got, err := EncodeHTTPResponse(HTTPResponse{
		Status:  200,
		Headers: "content-type: application/json\r\n",
		Body:    []byte(`{"ok":true}`),
	})
	if err != nil {
		t.Fatal(err)
	}
	if hex.EncodeToString(got) != hex.EncodeToString(want) {
		t.Fatalf("res mismatch\n got %x\nwant %x", got, want)
	}
	res, err := DecodeHTTPResponse(want)
	if err != nil {
		t.Fatal(err)
	}
	if res.Status != 200 || string(res.Body) != `{"ok":true}` {
		t.Fatalf("res=%+v", res)
	}
}

func TestFragmentInnerFixture(t *testing.T) {
	fix := loadInnerFixture(t)
	rawFrags := fix["fragment_max_plain_30_payload_50"].([]any)
	payload := make([]byte, 50)
	for i := range payload {
		payload[i] = 'x'
	}
	got, err := FragmentInner(TypeHTTPRes, 9, payload, 30, true)
	if err != nil {
		t.Fatal(err)
	}
	if len(got) != len(rawFrags) {
		t.Fatalf("frag count %d want %d", len(got), len(rawFrags))
	}
	for i, g := range got {
		want := mustHex(t, rawFrags[i].(string))
		if hex.EncodeToString(g) != hex.EncodeToString(want) {
			t.Fatalf("frag[%d]\n got %x\nwant %x", i, g, want)
		}
		frame, err := DecodeInner(g)
		if err != nil {
			t.Fatal(err)
		}
		if i == len(got)-1 {
			if !frame.Fin() {
				t.Fatal("last fragment must set FIN")
			}
		} else if frame.Fin() {
			t.Fatal("non-final fragment must clear FIN")
		}
	}
}

func TestDecodeInnerFailClosed(t *testing.T) {
	badVersion := make([]byte, InnerHdrLen)
	badVersion[0] = 2
	short := make([]byte, InnerHdrLen)
	short[0] = InnerVersion
	short[1] = TypePing
	binary.BigEndian.PutUint32(short[7:11], 5) // claims 5 bytes, none present
	cases := [][]byte{
		{},
		{1, 1},
		badVersion,
		short,
	}
	for i, c := range cases {
		if _, err := DecodeInner(c); err == nil {
			t.Fatalf("case %d expected error", i)
		}
	}
}

func TestAbsoluteURIRefused(t *testing.T) {
	payload, err := EncodeHTTPRequest(HTTPRequest{Method: "GET", Target: "https://evil/x"})
	if err != nil {
		t.Fatal(err)
	}
	if _, err := DecodeHTTPRequest(payload); err == nil {
		t.Fatal("expected absolute URI refusal")
	}
}

func TestFragmentFitsMaxPlaintext(t *testing.T) {
	// Mirror Android maxFragmentFitsHostRecordLimit: each fragment <= MaxPlaintext.
	payload := make([]byte, MaxPlaintext*2+100)
	frames, err := FragmentInner(TypeHTTPRes, 1, payload, MaxPlaintext, true)
	if err != nil {
		t.Fatal(err)
	}
	for i, f := range frames {
		if len(f) > MaxPlaintext {
			t.Fatalf("frag %d len %d > MaxPlaintext", i, len(f))
		}
	}
}
