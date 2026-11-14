package httpapi

import (
	"sort"
	"testing"
	"time"
)

func TestFormatISOIsFixedWidthAndSortsChronologically(t *testing.T) {
	base := time.Date(2026, 11, 13, 6, 56, 32, 0, time.UTC)
	times := []time.Time{
		base,
		base.Add(350 * time.Millisecond),      // .350000 — trailing zeros
		base.Add(351200 * time.Microsecond),   // .351200
		base.Add(351234567 * time.Nanosecond), // sub-microsecond truncated
		base.Add(time.Second),                 // next whole second
		base.Add(time.Second + time.Microsecond),
	}
	var stamps []string
	for _, tm := range times {
		s := formatISO(tm)
		if len(s) != len("2026-11-13T06:56:32.000000Z") {
			t.Fatalf("stamp %q is not fixed width", s)
		}
		stamps = append(stamps, s)
	}
	if !sort.StringsAreSorted(stamps) {
		t.Fatalf("text order differs from time order: %v", stamps)
	}
	if _, err := time.Parse(time.RFC3339Nano, stamps[1]); err != nil {
		t.Fatalf("stamp does not parse as RFC3339: %v", err)
	}
}

func TestFormatISOConvertsToUTC(t *testing.T) {
	loc := time.FixedZone("UTC-6", -6*3600)
	got := formatISO(time.Date(2026, 11, 13, 0, 0, 0, 0, loc))
	if got != "2026-11-13T06:00:00.000000Z" {
		t.Fatalf("got %q", got)
	}
}
