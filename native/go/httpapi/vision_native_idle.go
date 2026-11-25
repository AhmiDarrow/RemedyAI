package httpapi

// Idle auto-stop (vision.runtime.mark_used / ensure_idle_watcher /
// maybe_idle_stop) and the -ngl rule (mind_engine.mind_n_gpu_layers).

import (
	"math"
	"strings"
	"time"
)

// NoteUse records that the vision server just served a request (Python
// mark_used). Call it wherever the runtime sends a request to the vision port.
func (w *nativeVisionWorker) NoteUse() {
	w.idleMu.Lock()
	w.lastUsed = w.now()
	w.idleMu.Unlock()
}

// Close stops the idle watcher. It does not stop the model server.
func (w *nativeVisionWorker) Close() {
	w.idleMu.Lock()
	if w.idleQuit != nil {
		close(w.idleQuit)
		w.idleQuit = nil
	}
	w.idleMu.Unlock()
}

// nvIdleLimit is vision.idle_stop_s. Python reads `int(x or 600)`, so unset
// and 0 both mean 600 s; a negative value disables the idle stop.
func nvIdleLimit(home string) time.Duration {
	vt, _ := asStringMap(LoadConfig(home)["vision"])
	n := 600
	if vt != nil {
		if v := anyInt(vt["idle_stop_s"], 0); v != 0 {
			n = v
		}
	}
	return time.Duration(n) * time.Second
}

// ensureIdleWatcher starts the background poller once.
func (w *nativeVisionWorker) ensureIdleWatcher(home string) {
	w.idleMu.Lock()
	defer w.idleMu.Unlock()
	w.idleHome = home
	if w.idleQuit != nil {
		return
	}
	quit := make(chan struct{})
	w.idleQuit = quit
	every := w.idleEvery
	go func() {
		t := time.NewTicker(every)
		defer t.Stop()
		for {
			select {
			case <-quit:
				return
			case <-t.C:
				w.idleMu.Lock()
				h := w.idleHome
				w.idleMu.Unlock()
				w.idleTick(h)
			}
		}
	}()
}

// idleTick is maybe_idle_stop: stop the server once it has been unused for
// the configured period. A server never marked used gets marked now.
func (w *nativeVisionWorker) idleTick(home string) map[string]any {
	limit := nvIdleLimit(home)
	if limit < 0 {
		return map[string]any{"ok": true, "stopped": false, "reason": "disabled"}
	}
	state := loadVisionJSON(home)
	host := firstNonEmpty(anyString(state["host"]), visionDefaultHost)
	if !visionPortOpen(host, anyInt(state["port"], visionDefaultPort)) {
		return map[string]any{"ok": true, "stopped": false, "reason": "not_running"}
	}
	w.idleMu.Lock()
	last := w.lastUsed
	w.idleMu.Unlock()
	if last.IsZero() {
		w.NoteUse()
		return map[string]any{"ok": true, "stopped": false, "reason": "first_mark"}
	}
	age := w.now().Sub(last)
	idle := math.Round(age.Seconds()*10) / 10
	if age < limit {
		return map[string]any{"ok": true, "stopped": false, "reason": "active",
			"idle_s": idle, "limit_s": int(limit.Seconds())}
	}
	out, _ := w.stopServer(home)
	out["reason"] = "idle_timeout"
	out["idle_s"] = idle
	return out
}

// nvMindGPULayers is mind_n_gpu_layers. While a local muscle (RMB) runs, the
// mind follows the co-residence plan the muscle stored in rmb.json
// (last_coresidence.mind_n_gpu_layers, written when RMB started); with no plan
// it yields to CPU unless mind_keep_gpu is on. requested is the -ngl the
// caller wants (vision.n_gpu_layers, default -1).
func nvMindGPULayers(home string, cfg ConfigMap, requested int) int {
	rmb := nvRMBRunning(home)
	if requested >= 0 && !rmb {
		return requested
	}
	if rmb {
		keep := nvKnobBool(cfg, "mind_keep_gpu")
		if last := lastCoresidenceMap(home); last != nil && last["mind_n_gpu_layers"] != nil {
			// A plan stored before mind_keep_gpu was switched on must not
			// evict the mind: re-planning would pick keep_mind.
			if !(keep && strings.EqualFold(anyString(last["mode"]), "yield_mind")) {
				return anyInt(last["mind_n_gpu_layers"], requested)
			}
		} else if !keep {
			return 0
		}
	}
	return requested
}

// nvRMBRunning is rmb.mode.rmb_server_running: the managed RMB server port is
// open (starting or serving).
func nvRMBRunning(home string) bool {
	st := mergeRmbState(loadRmbJSON(home))
	return rmbPortOpen(anyString(st["host"]), anyInt(st["port"], rmbDefaultPort))
}
