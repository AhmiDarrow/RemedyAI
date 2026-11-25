package connect

import (
	"bytes"
	"encoding/hex"
	"testing"
)

func fill(b byte) []byte { return bytes.Repeat([]byte{b}, 32) }

// The phone (connect/core, Rust) derives the same identity from its side of
// the key agreement. This vector is asserted there too
// (keys::tests::direct_seed_matches_the_pc).
func TestDirectIdentityIsTheSameFromBothSides(t *testing.T) {
	device, err := KeyPairFromPrivate(fill(1))
	if err != nil {
		t.Fatal(err)
	}
	host, err := KeyPairFromPrivate(fill(2))
	if err != nil {
		t.Fatal(err)
	}
	fromPC, err := DirectIdentitySeed(host.Private, device.Public)
	if err != nil {
		t.Fatal(err)
	}
	fromPhone, err := DirectIdentitySeed(device.Private, host.Public)
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(fromPC, fromPhone) {
		t.Fatal("the two sides derive different seeds")
	}
	id, err := DirectEndpointID(host.Private, device.Public)
	if err != nil {
		t.Fatal(err)
	}
	t.Logf("seed=%s id=%s", hex.EncodeToString(fromPC), id)
	const wantSeed = "fbb25fb6f96dedc95fc0f24be0620f51019fdd55a7ff395ea935b25fcefdc4b7"
	const wantID = "839dfa90bf6c77fc84a2a3af9dc54fb95847b1e9baf71d996a79c0d6ce69ecae"
	if hex.EncodeToString(fromPC) != wantSeed || id != wantID {
		t.Fatalf("vector changed: seed=%s id=%s", hex.EncodeToString(fromPC), id)
	}
	// Another PC, or another phone, is a different identity.
	other, _ := KeyPairFromPrivate(fill(3))
	if a, _ := DirectEndpointID(other.Private, device.Public); a == id {
		t.Fatal("the identity must depend on the PC")
	}
	if b, _ := DirectEndpointID(host.Private, other.Public); b == id {
		t.Fatal("the identity must depend on the phone")
	}
}

func TestDirectAllowListIsThePairedUnrevokedPhones(t *testing.T) {
	home := t.TempDir()
	host, err := LoadOrCreateHostKeyPair(home)
	if err != nil {
		t.Fatal(err)
	}
	if ids, err := DirectAllowList(home); err != nil || len(ids) != 0 {
		t.Fatalf("no phones paired: %v %v", ids, err)
	}
	a, _ := KeyPairFromPrivate(fill(5))
	b, _ := KeyPairFromPrivate(fill(6))
	for i, kp := range []KeyPair{a, b} {
		_, err := SaveDevice(Device{
			ID: hex.EncodeToString(bytes.Repeat([]byte{byte(0xa0 + i)}, 16)), Name: "phone",
			PublicHex: hex.EncodeToString(kp.Public), Revoked: i == 1,
		}, home)
		if err != nil {
			t.Fatal(err)
		}
	}
	ids, err := DirectAllowList(home)
	if err != nil {
		t.Fatal(err)
	}
	want, _ := DirectEndpointID(host.Private, a.Public)
	if len(ids) != 1 || ids[0] != want {
		t.Fatalf("allow list %v, want only %s (the revoked phone is out)", ids, want)
	}
}
