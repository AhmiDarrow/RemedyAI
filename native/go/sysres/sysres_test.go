package sysres

import "testing"

func TestParseMeminfoPrefersMemAvailable(t *testing.T) {
	total, avail := parseMeminfo("MemTotal:       16000000 kB\nMemFree:  1000 kB\nMemAvailable:    8000000 kB\nCached: 5 kB\n")
	if total != 16000000*1024 || avail != 8000000*1024 {
		t.Fatalf("got total=%d avail=%d", total, avail)
	}
}

func TestParseMeminfoFallsBackToFreePlusCached(t *testing.T) {
	total, avail := parseMeminfo("MemTotal: 100 kB\nMemFree: 20 kB\nCached: 30 kB\n")
	if total != 100*1024 || avail != 50*1024 {
		t.Fatalf("got total=%d avail=%d", total, avail)
	}
}

func TestParseMeminfoGarbageIsUnknown(t *testing.T) {
	if total, avail := parseMeminfo("nonsense\n"); total != 0 || avail != 0 {
		t.Fatalf("garbage must read as unknown, got %d/%d", total, avail)
	}
	if (Snapshot{}).AvailableFraction() != 1 {
		t.Fatal("unknown memory must not look full")
	}
}

func TestReadReportsCPUs(t *testing.T) {
	s := Read()
	if s.LogicalCPUs < 1 {
		t.Fatalf("cpu count %d", s.LogicalCPUs)
	}
	if s.Known() && s.AvailableBytes > s.TotalBytes {
		t.Fatalf("available %d > total %d", s.AvailableBytes, s.TotalBytes)
	}
}
