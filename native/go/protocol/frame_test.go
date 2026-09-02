package protocol

import (
	"bytes"
	"errors"
	"io"
	"testing"
)

func TestFrameGoldenAndRoundTrip(t *testing.T) {
	wantID := [16]byte{0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15}
	raw, err := (Frame{
		Kind:          KindToolRequest,
		Flags:         5,
		CorrelationID: wantID,
		Payload:       []byte("ping"),
	}).MarshalBinary()
	if err != nil {
		t.Fatal(err)
	}
	want := []byte{
		'R', 'M', 'D', 'Y', 1, 0, 1, 0, 5, 0, 0, 0, 4, 0, 0, 0,
		0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15,
		'p', 'i', 'n', 'g',
	}
	if string(raw) != string(want) {
		t.Fatalf("golden frame mismatch: %x", raw)
	}
	got, err := Parse(raw)
	if err != nil {
		t.Fatal(err)
	}
	if got.Kind != KindToolRequest || got.Flags != 5 || got.CorrelationID != wantID || string(got.Payload) != "ping" {
		t.Fatalf("unexpected round trip: %#v", got)
	}
}

func TestStreamReadWriteAndTruncation(t *testing.T) {
	var wire bytes.Buffer
	want := Frame{Kind: KindHealth, Payload: []byte("ready")}
	if err := WriteFrame(&wire, want); err != nil {
		t.Fatal(err)
	}
	got, err := ReadFrame(&wire)
	if err != nil {
		t.Fatal(err)
	}
	if got.Kind != want.Kind || string(got.Payload) != "ready" {
		t.Fatalf("frame = %#v", got)
	}
	raw, _ := want.MarshalBinary()
	if _, err := ReadFrame(bytes.NewReader(raw[:len(raw)-1])); !errors.Is(err, io.ErrUnexpectedEOF) {
		t.Fatalf("truncated stream: %v", err)
	}
}

func TestParseRejectsMalformedFamily(t *testing.T) {
	base, err := (Frame{Kind: KindEvent, Payload: []byte("x")}).MarshalBinary()
	if err != nil {
		t.Fatal(err)
	}
	tests := []struct {
		name string
		raw  []byte
		want error
	}{
		{"short", base[:HeaderSize-1], ErrShortFrame},
		{"magic", append([]byte("NOPE"), base[4:]...), ErrBadMagic},
		{"version", append([]byte(nil), base...), ErrUnsupported},
		{"kind", append([]byte(nil), base...), ErrInvalidKind},
		{"length", append([]byte(nil), base...), ErrPayloadTruncated},
	}
	tests[2].raw[4] = 2
	tests[3].raw[6], tests[3].raw[7] = 0, 0
	tests[4].raw[12] = 2
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			_, err := Parse(tt.raw)
			if !errors.Is(err, tt.want) {
				t.Fatalf("got %v, want %v", err, tt.want)
			}
		})
	}
}

func BenchmarkFrameRoundTrip(b *testing.B) {
	f := Frame{Kind: KindToolResult, Payload: make([]byte, 1024)}
	b.ReportAllocs()
	for range b.N {
		raw, err := f.MarshalBinary()
		if err != nil {
			b.Fatal(err)
		}
		if _, err := Parse(raw); err != nil {
			b.Fatal(err)
		}
	}
}
