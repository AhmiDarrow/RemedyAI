package connect

import (
	"encoding/hex"
	"testing"
)

func TestSaveAndListDevice(t *testing.T) {
	home := t.TempDir()
	pub := bytesFilled(0x42)
	rec := Device{
		ID:        deviceIDFor(pub),
		Name:      "kitchen",
		PublicHex: hex.EncodeToString(pub),
		PairedAt:  1_700_000_000,
		Revoked:   false,
	}
	saved, err := SaveDevice(rec, home)
	if err != nil {
		t.Fatal(err)
	}
	if saved.ID != rec.ID || saved.Name != "kitchen" {
		t.Fatalf("%+v", saved)
	}
	got, err := GetDevice(rec.ID, home)
	if err != nil || got == nil {
		t.Fatalf("get: %+v err=%v", got, err)
	}
	if got.PublicHex != rec.PublicHex {
		t.Fatal("public mismatch")
	}
	found, err := FindDeviceByPublic(rec.PublicHex, home)
	if err != nil || found == nil || found.ID != rec.ID {
		t.Fatalf("find: %+v err=%v", found, err)
	}
	n, err := ActiveDeviceCount(home)
	if err != nil || n != 1 {
		t.Fatalf("active=%d err=%v", n, err)
	}
}

func TestRevokeHidesFromActive(t *testing.T) {
	home := t.TempDir()
	pub := bytesFilled(0x43)
	rec := Device{ID: deviceIDFor(pub), Name: "phone", PublicHex: hex.EncodeToString(pub)}
	if _, err := SaveDevice(rec, home); err != nil {
		t.Fatal(err)
	}
	if _, err := RevokeDevice(rec.ID, home); err != nil {
		t.Fatal(err)
	}
	n, _ := ActiveDeviceCount(home)
	if n != 0 {
		t.Fatalf("active=%d", n)
	}
	if !IsRevokedLive(rec.ID) {
		t.Fatal("expected live revoke mark")
	}
	list, err := ListDevices(home, true)
	if err != nil || len(list) != 1 || !list[0].Revoked {
		t.Fatalf("revoked list=%+v err=%v", list, err)
	}
}

func TestPauseCacheFollowsSetPaused(t *testing.T) {
	home := t.TempDir()
	if IsPaused(home) {
		t.Fatal("default paused")
	}
	if err := SetPaused(true, home); err != nil {
		t.Fatal(err)
	}
	if !IsPaused(home) {
		t.Fatal("expected paused")
	}
	if err := SetPaused(false, home); err != nil {
		t.Fatal(err)
	}
	if IsPaused(home) {
		t.Fatal("expected unpaused")
	}
}

func TestDevicePublicMetaOmitsPublicHex(t *testing.T) {
	home := t.TempDir()
	pub := bytesFilled(0x44)
	rec := Device{
		ID:        deviceIDFor(pub),
		Name:      "pixel",
		PublicHex: hex.EncodeToString(pub),
		PairedAt:  1_725_000_000.5,
	}
	if _, err := SaveDevice(rec, home); err != nil {
		t.Fatal(err)
	}
	rows := DevicePublicMetaList(home)
	if len(rows) != 1 {
		t.Fatalf("%+v", rows)
	}
	if rows[0].ID != rec.ID || rows[0].Name != "pixel" || rows[0].PairedAt != rec.PairedAt {
		t.Fatalf("%+v", rows[0])
	}
	if rows[0].Revoked {
		t.Fatal("revoked")
	}
	if _, err := RevokeDevice(rec.ID, home); err != nil {
		t.Fatal(err)
	}
	if got := DevicePublicMetaList(home); len(got) != 0 {
		t.Fatalf("revoked still visible: %+v", got)
	}
}
