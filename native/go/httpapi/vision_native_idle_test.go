package httpapi

import (
	"context"
	"fmt"
	"net"
	"os"
	"path/filepath"
	"strconv"
	"testing"
	"time"
)

// nvStartedFake starts the fake llama-server through the worker.
func nvStartedFake(t *testing.T, every time.Duration) (*nvFixture, int) {
	t.Helper()
	fake := nvBuildFakeServer(t)
	f := newNVFixture(t, nil)
	nvSeedInstall(t, f)
	data, _ := os.ReadFile(fake)
	bin := filepath.Join(visionRuntimeDir(f.home), nvTestBinName())
	os.WriteFile(bin, data, 0o755)
	port := nvFreePort(t)
	writeJSONAtomic(visionJSONPath(f.home), map[string]any{
		"model_id": visionDefaultModelID, "model_path": f.modelPath(),
		"mmproj_path":    filepath.Join(filepath.Dir(f.modelPath()), nvTestMMProjFile),
		"runtime_binary": bin, "host": "127.0.0.1", "port": port,
		"base_url": fmt.Sprintf("http://127.0.0.1:%d/v1", port),
	})
	nativeVisionSpawn = nvExecSpawn
	f.w.idleEvery = every
	t.Cleanup(func() { f.w.Close(); f.w.Stop(context.Background(), f.home) })
	if res, _ := f.w.Start(context.Background(), f.home); res["ok"] != true {
		t.Fatalf("start: %v", res)
	}
	return f, port
}

func nvWriteConfig(t *testing.T, home, body string) {
	t.Helper()
	if err := os.WriteFile(filepath.Join(home, "config.toml"), []byte(body), 0o644); err != nil {
		t.Fatal(err)
	}
	InvalidateConfigCache()
}

func TestVisionNativeIdleStopWithFakeClock(t *testing.T) {
	f, port := nvStartedFake(t, time.Hour) // watcher effectively off; tick by hand
	t0 := time.Now()
	clock := t0
	f.w.now = func() time.Time { return clock }
	f.w.NoteUse()

	clock = t0.Add(599 * time.Second) // default limit is 600 s
	if r := f.w.idleTick(f.home); r["stopped"] != false || r["reason"] != "active" {
		t.Fatalf("%v", r)
	}
	clock = t0.Add(500 * time.Second)
	f.w.NoteUse() // a request resets the timer
	clock = t0.Add(1000 * time.Second)
	if r := f.w.idleTick(f.home); r["stopped"] != false {
		t.Fatalf("stopped despite recent use: %v", r)
	}
	clock = t0.Add(1200 * time.Second)
	r := f.w.idleTick(f.home)
	if r["stopped"] != true || r["reason"] != "idle_timeout" {
		t.Fatalf("%v", r)
	}
	deadline := time.Now().Add(5 * time.Second)
	for visionPortOpen("127.0.0.1", port) {
		if time.Now().After(deadline) {
			t.Fatal("server still up")
		}
		time.Sleep(20 * time.Millisecond)
	}
	if r := f.w.idleTick(f.home); r["reason"] != "not_running" {
		t.Fatalf("%v", r)
	}
}

func TestVisionNativeIdleConfig(t *testing.T) {
	home := t.TempDir()
	t.Setenv("REMEDY_HOME", home)
	if nvIdleLimit(home) != 600*time.Second {
		t.Fatal("default must be 600 s")
	}
	nvWriteConfig(t, home, "[vision]\nidle_stop_s = 0\n")
	if nvIdleLimit(home) != 600*time.Second {
		t.Fatal("0 means 600 in the Python (`x or 600`)")
	}
	nvWriteConfig(t, home, "[vision]\nidle_stop_s = 45\n")
	if nvIdleLimit(home) != 45*time.Second {
		t.Fatal(nvIdleLimit(home))
	}
	nvWriteConfig(t, home, "[vision]\nidle_stop_s = -1\n")
	w := newNativeVisionWorker()
	if r := w.idleTick(home); r["reason"] != "disabled" {
		t.Fatalf("%v", r)
	}
}

func TestVisionNativeIdleWatcherStopsServer(t *testing.T) {
	f, port := nvStartedFake(t, 20*time.Millisecond)
	nvWriteConfig(t, f.home, "[vision]\nidle_stop_s = 1\n")
	deadline := time.Now().Add(6 * time.Second)
	for visionPortOpen("127.0.0.1", port) {
		if time.Now().After(deadline) {
			t.Fatal("watcher never stopped the idle server")
		}
		time.Sleep(50 * time.Millisecond)
	}
}

func TestVisionNativeGPULayersWithMuscle(t *testing.T) {
	home := t.TempDir()
	t.Setenv("REMEDY_HOME", home)
	cfg := ConfigMap{}
	if nvMindGPULayers(home, cfg, -1) != -1 || nvMindGPULayers(home, cfg, 20) != 20 {
		t.Fatal("no muscle: requested layers are used")
	}
	l, err := net.Listen("tcp", "127.0.0.1:0") // stands in for the RMB server
	if err != nil {
		t.Fatal(err)
	}
	defer l.Close()
	rmbPort := l.Addr().(*net.TCPAddr).Port
	writeJSONAtomic(rmbJSONPath(home), map[string]any{"host": "127.0.0.1", "port": rmbPort})
	if !nvRMBRunning(home) {
		t.Fatal("muscle not seen")
	}
	if got := nvMindGPULayers(home, cfg, -1); got != 0 {
		t.Fatalf("muscle running, no plan: mind yields to CPU, got %d", got)
	}
	nvWriteConfig(t, home, "[advanced]\nmind_keep_gpu = true\n")
	if got := nvMindGPULayers(home, LoadConfig(home), -1); got != -1 {
		t.Fatalf("mind_keep_gpu keeps the mind on the GPU, got %d", got)
	}
	writeJSONAtomic(rmbJSONPath(home), map[string]any{"host": "127.0.0.1", "port": rmbPort,
		"last_coresidence": map[string]any{"mode": "share", "mind_n_gpu_layers": -1}})
	if got := nvMindGPULayers(home, ConfigMap{}, 0); got != -1 {
		t.Fatalf("stored share plan wins over the request, got %d", got)
	}
	writeJSONAtomic(rmbJSONPath(home), map[string]any{"host": "127.0.0.1", "port": rmbPort,
		"last_coresidence": map[string]any{"mode": "yield_mind", "mind_n_gpu_layers": 0}})
	if got := nvMindGPULayers(home, ConfigMap{}, -1); got != 0 {
		t.Fatalf("stored yield plan, got %d", got)
	}
	if got := nvMindGPULayers(home, LoadConfig(home), -1); got != -1 { // keep_gpu overrides a stale yield plan
		t.Fatalf("keep_gpu vs stale yield plan, got %d", got)
	}
	l.Close()
	if got := nvMindGPULayers(home, ConfigMap{}, 20); got != 20 {
		t.Fatalf("muscle gone: requested again, got %d", got)
	}
}

func TestVisionNativeStopRefusesUnverifiedRecordedPID(t *testing.T) {
	f := newNVFixture(t, nil)
	self := os.Getpid() // alive, and certainly not a llama-server
	var killed []uint32
	old := nativeVisionKill
	nativeVisionKill = func(pid uint32) error { killed = append(killed, pid); return nil }
	t.Cleanup(func() { nativeVisionKill = old })
	writeJSONAtomic(visionJSONPath(f.home), map[string]any{"pid": self})
	res, _ := f.w.Stop(context.Background(), f.home)
	if len(killed) != 0 || res["stopped"] != false {
		t.Fatalf("killed an unverified process: %v %v", killed, res)
	}
	stale, _ := res["stale_records"].([]int)
	if len(stale) != 1 || stale[0] != self || res["message"] == nil {
		t.Fatalf("stale record not reported: %v", res)
	}
	if loadVisionJSON(f.home)["pid"] != nil {
		t.Fatal("record kept")
	}
	// A verified record is killed.
	oldVerify := nativeVisionVerifyPID
	nativeVisionVerifyPID = func(int) bool { return true }
	t.Cleanup(func() { nativeVisionVerifyPID = oldVerify })
	writeJSONAtomic(visionJSONPath(f.home), map[string]any{"pid": self})
	res, _ = f.w.Stop(context.Background(), f.home)
	if len(killed) != 1 || killed[0] != uint32(self) || res["stopped"] != true || res["stale_records"] != nil {
		t.Fatalf("verified pid not killed: %v %v", killed, res)
	}
	_ = strconv.Itoa
}
