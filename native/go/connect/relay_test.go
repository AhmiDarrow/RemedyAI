package connect_test

import (
	"context"
	"encoding/binary"
	"errors"
	"io"
	"net"
	"strconv"
	"testing"
	"time"

	"github.com/AhmiDarrow/RemedyAI/native/go/connect"
)

func TestRelayWildcardBindRefused(t *testing.T) {
	for _, host := range []string{"0.0.0.0", "*", "::", "[::]"} {
		if _, err := connect.StartRelay(host, 0, time.Second); !errors.Is(err, connect.ErrBind) && !errors.Is(err, connect.ErrRelay) {
			t.Fatalf("%q: want ErrBind/ErrRelay, got %v", host, err)
		}
		if _, err := connect.AssertRelayBind(host); err == nil {
			t.Fatalf("%q: AssertRelayBind must refuse", host)
		}
	}
}

func TestRelayChosenBindAllowsLoopback(t *testing.T) {
	got, err := connect.AssertRelayBind("127.0.0.1")
	if err != nil || got != "127.0.0.1" {
		t.Fatalf("got %q err=%v", got, err)
	}
}

func TestRelayForwardsFramesEqualNoAEAD(t *testing.T) {
	r, err := connect.StartRelay("127.0.0.1", 0, 5*time.Second)
	if err != nil {
		t.Fatal(err)
	}
	defer r.Stop()

	sid := []byte("0123456789abcdef")
	if len(sid) != connect.SessionIDLen {
		t.Fatal("sid len")
	}

	a, err := net.DialTimeout("tcp", r.Addr(), 5*time.Second)
	if err != nil {
		t.Fatal(err)
	}
	defer a.Close()
	b, err := net.DialTimeout("tcp", r.Addr(), 5*time.Second)
	if err != nil {
		t.Fatal(err)
	}
	defer b.Close()
	_ = a.SetDeadline(time.Now().Add(5 * time.Second))
	_ = b.SetDeadline(time.Now().Add(5 * time.Second))

	if _, err := a.Write(sid); err != nil {
		t.Fatal(err)
	}
	if _, err := b.Write(sid); err != nil {
		t.Fatal(err)
	}

	payload := []byte("hello")
	frame := frameBlob(payload)
	if _, err := a.Write(frame); err != nil {
		t.Fatal(err)
	}
	got := mustReadFull(t, b, len(frame))
	if string(got) != string(frame) {
		t.Fatalf("a→b: got %q want %q", got, frame)
	}

	blob := make([]byte, 0, 256+128)
	for i := 0; i < 256; i++ {
		blob = append(blob, byte(i))
	}
	for i := 0; i < 64; i++ {
		blob = append(blob, 0x00, 0xff)
	}
	frame2 := frameBlob(blob)
	if _, err := b.Write(frame2); err != nil {
		t.Fatal(err)
	}
	got2 := mustReadFull(t, a, len(frame2))
	if string(got2) != string(frame2) {
		t.Fatalf("b→a mismatch len=%d/%d", len(got2), len(frame2))
	}
}

func TestRelayOversizeFrameDropped(t *testing.T) {
	r, err := connect.StartRelay("127.0.0.1", 0, 5*time.Second)
	if err != nil {
		t.Fatal(err)
	}
	defer r.Stop()

	sid := []byte("abcdefghijklmnop")
	a, err := net.DialTimeout("tcp", r.Addr(), 5*time.Second)
	if err != nil {
		t.Fatal(err)
	}
	defer a.Close()
	b, err := net.DialTimeout("tcp", r.Addr(), 5*time.Second)
	if err != nil {
		t.Fatal(err)
	}
	defer b.Close()
	_ = a.SetDeadline(time.Now().Add(2 * time.Second))
	_ = b.SetDeadline(time.Now().Add(2 * time.Second))
	if _, err := a.Write(sid); err != nil {
		t.Fatal(err)
	}
	if _, err := b.Write(sid); err != nil {
		t.Fatal(err)
	}

	hdr := make([]byte, 4)
	binary.BigEndian.PutUint32(hdr, connect.RelayMaxPayload+1)
	if _, err := a.Write(append(hdr, 'x')); err != nil {
		t.Fatal(err)
	}
	buf := make([]byte, 16)
	n, err := b.Read(buf)
	if n != 0 && !errors.Is(err, io.EOF) {
		// Timeout or clean close with no bytes is the success case.
		if ne, ok := err.(net.Error); !(ok && ne.Timeout()) && err != nil {
			t.Fatalf("unexpected read n=%d err=%v", n, err)
		}
	}
	if n != 0 {
		t.Fatalf("oversize frame leaked %d bytes", n)
	}
}

func TestRelayStopIdempotent(t *testing.T) {
	r, err := connect.StartRelay("127.0.0.1", 0, time.Second)
	if err != nil {
		t.Fatal(err)
	}
	r.Stop()
	r.Stop()
}

func TestDialRelayWritesSessionID(t *testing.T) {
	r, err := connect.StartRelay("127.0.0.1", 0, 5*time.Second)
	if err != nil {
		t.Fatal(err)
	}
	defer r.Stop()

	sid := []byte("0123456789abcdef")
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	url := "127.0.0.1:" + strconv.Itoa(r.Port())
	a, err := connect.DialRelay(ctx, url, sid)
	if err != nil {
		t.Fatal(err)
	}
	defer a.Close()
	b, err := connect.DialRelay(ctx, url, sid)
	if err != nil {
		t.Fatal(err)
	}
	defer b.Close()

	frame := frameBlob([]byte("ping"))
	if _, err := a.Write(frame); err != nil {
		t.Fatal(err)
	}
	_ = b.SetDeadline(time.Now().Add(3 * time.Second))
	got := mustReadFull(t, b, len(frame))
	if string(got) != string(frame) {
		t.Fatalf("got %q", got)
	}
}

func TestDialRelayRejectsBadSessionID(t *testing.T) {
	ctx := context.Background()
	if _, err := connect.DialRelay(ctx, "127.0.0.1:9", []byte("short")); !errors.Is(err, connect.ErrRelay) {
		t.Fatalf("want ErrRelay, got %v", err)
	}
}

func TestRelayConfigured(t *testing.T) {
	got, err := connect.RelayConfigured("")
	if err != nil || got != "" {
		t.Fatalf("empty: %q %v", got, err)
	}
	got, err = connect.RelayConfigured("  10.0.0.8:7402 ")
	if err != nil || got != "10.0.0.8:7402" {
		t.Fatalf("got %q err=%v", got, err)
	}
	if _, err := connect.RelayConfigured("https://evil.example/relay"); err == nil {
		t.Fatal("http relay must fail closed")
	}
}

func frameBlob(payload []byte) []byte {
	hdr := make([]byte, 4)
	binary.BigEndian.PutUint32(hdr, uint32(len(payload)))
	return append(hdr, payload...)
}

func mustReadFull(t *testing.T, r io.Reader, n int) []byte {
	t.Helper()
	buf := make([]byte, n)
	if _, err := io.ReadFull(r, buf); err != nil {
		t.Fatal(err)
	}
	return buf
}
