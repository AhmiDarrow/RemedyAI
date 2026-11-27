package httpapi

import (
	"context"
	"fmt"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"time"
)

// nativeRmbController runs the local model host: a llama-server child started
// through the Zig host like every other process the runtime owns, so it dies
// with the runtime and never shows a console window.
type nativeRmbController struct {
	mu   sync.Mutex // one start or stop at a time
	proc *visionProc
}

// NewNativeRmbController returns the controller the runtime serves local
// models with.
func NewNativeRmbController() RmbController { return &nativeRmbController{} }

// rmbStartWait is how long a model may take to load before Start gives up.
// Large models read tens of gigabytes from disk.
var rmbStartWait = 10 * time.Minute

func (c *nativeRmbController) Start(ctx context.Context, homeDir string) (map[string]any, error) {
	c.mu.Lock()
	defer c.mu.Unlock()
	home := ResolveHomeDir(homeDir)
	state := mergeRmbState(loadRmbJSON(home))
	host := firstNonEmpty(anyString(state["host"]), rmbDefaultHost)
	port := anyInt(state["port"], rmbDefaultPort)
	base := firstNonEmpty(anyString(state["base_url"]), fmt.Sprintf("http://%s:%d/v1", host, port))

	if rmbPortOpen(host, port) {
		if ready, loading := rmbHealthOK(base); ready || loading {
			return map[string]any{"ok": true, "already_running": true, "base_url": base}, nil
		}
		return map[string]any{"ok": false,
			"error": fmt.Sprintf("Port %d is in use by another program. Change the port in Local models.", port)}, nil
	}
	model := resolveRmbModelPath(state, home)
	if model == "" {
		return map[string]any{"ok": false, "error": "No model file. Download one in Local models first."}, nil
	}
	binary := findLlamaBinary(state, home)
	if binary == "" {
		return map[string]any{"ok": false, "error": "llama-server is not installed. Install the local runtime first."}, nil
	}
	if abs, err := filepath.Abs(binary); err == nil {
		binary = abs
	}

	argv := rmbCommand(binary, model, host, port, state, nativeVisionHelp(home, binary))
	proc, err := nativeVisionSpawn(ctx, home, argv, filepath.Dir(binary))
	if err != nil {
		return map[string]any{"ok": false, "error": "Could not start llama-server: " + err.Error()}, nil
	}
	c.proc = proc
	state["pid"], state["user_stopped"] = int(proc.PID), false
	_ = saveRmbJSON(home, state)

	deadline := time.Now().Add(rmbStartWait)
	for time.Now().Before(deadline) {
		if ctx.Err() != nil {
			// The request went away; the model keeps loading and status shows it.
			return map[string]any{"ok": true, "starting": true, "base_url": base, "pid": int(proc.PID)}, nil
		}
		if code, exited := proc.Exited(); exited {
			_ = proc.Kill()
			c.proc = nil
			delete(state, "pid")
			_ = saveRmbJSON(home, state)
			return map[string]any{"ok": false,
				"error": fmt.Sprintf("llama-server stopped while loading (exit code %d). The model may not fit in memory.", code)}, nil
		}
		if ready, _ := rmbHealthOK(base); ready {
			return map[string]any{"ok": true, "already_running": false, "base_url": base, "pid": int(proc.PID)}, nil
		}
		time.Sleep(nativeVisionPoll)
	}
	return map[string]any{"ok": false,
		"error": fmt.Sprintf("The model did not finish loading in %.0f minutes.", rmbStartWait.Minutes())}, nil
}

func (c *nativeRmbController) Stop(_ context.Context, homeDir string) (map[string]any, error) {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.stopLocked(ResolveHomeDir(homeDir)), nil
}

func (c *nativeRmbController) stopLocked(home string) map[string]any {
	stopped := false
	if c.proc != nil {
		_, exited := c.proc.Exited()
		// Kill also releases the handle of a child that already exited.
		stopped = c.proc.Kill() == nil && !exited
		c.proc = nil
	}
	state := mergeRmbState(loadRmbJSON(home))
	out := map[string]any{"ok": true}
	// A server an earlier runtime started is known only by its recorded PID.
	// It is stopped only when it can be verified as a llama-server.
	if pid := anyInt(state["pid"], -1); pid > 0 && !stopped && nvPIDAlive(pid) {
		if nativeVisionVerifyPID(pid) {
			stopped = nativeVisionKill(uint32(pid)) == nil
		} else {
			out["message"] = "A server is recorded for this port but could not be verified as llama-server, so it was left running."
		}
	}
	if state["pid"] != nil {
		state["pid"] = nil
		_ = saveRmbJSON(home, state)
	}
	out["stopped"] = stopped
	return out
}

// ApplyLive makes changed settings take effect. llama-server reads them at
// start, so a running host is restarted; a stopped one picks them up later.
func (c *nativeRmbController) ApplyLive(ctx context.Context, homeDir string, _ map[string]any) (map[string]any, error) {
	c.mu.Lock()
	running := c.proc != nil
	if running {
		if _, exited := c.proc.Exited(); exited {
			running = false
		}
	}
	if !running {
		c.mu.Unlock()
		return map[string]any{"live_apply": map[string]any{"live": false, "restarted": false}}, nil
	}
	c.stopLocked(ResolveHomeDir(homeDir))
	c.mu.Unlock()
	start, _ := c.Start(ctx, homeDir)
	apply := map[string]any{"live": true, "restarted": anyBoolDef(start["ok"], false)}
	if msg := anyString(start["error"]); msg != "" {
		apply["live_error"] = msg
	}
	return map[string]any{"live_apply": apply}, nil
}

// rmbCommand builds the llama-server command line from the saved settings.
// A flag the installed build does not list in its --help is left out.
func rmbCommand(binary, model, host string, port int, state map[string]any, help []byte) []string {
	num := func(key string) float64 { return anyFloat(state[key], 0) }
	whole := func(key string) int { return anyInt(state[key], 0) }
	text := func(key string) string { return strings.TrimSpace(anyString(state[key])) }
	on := func(key string) bool { return anyBoolDef(state[key], false) }
	dec := func(v float64) string { return strconv.FormatFloat(v, 'f', -1, 64) }

	cmd := []string{binary, "-m", model, "--host", host, "--port", strconv.Itoa(port),
		"--ctx-size", strconv.Itoa(max(2048, anyInt(state["ctx_size"], rmbDefaultCtx)))}
	// -1 leaves the split between GPU and CPU to llama-server.
	if ngl := anyInt(state["n_gpu_layers"], -1); ngl >= 0 {
		cmd = append(cmd, "-ngl", strconv.Itoa(ngl))
	}
	cmd = append(cmd, "--parallel", strconv.Itoa(max(1, anyInt(state["parallel"], 1))), "--cont-batching")
	if anyBoolDef(state["use_jinja"], true) {
		cmd = append(cmd, "--jinja")
	}
	if normalizeThinking(state["thinking"]) == "off" && nvHas(help, "--reasoning ") {
		cmd = append(cmd, "--reasoning", "off")
	}
	// Mixture-of-experts models keep their experts in system RAM.
	moe := whole("n_cpu_moe")
	if moe <= 0 {
		if id := catalogIDFromHint(filepath.Base(model)); id != "" {
			moe = rmbModelByID(id).NCPUMoE
		}
	}
	if moe > 0 && nvHas(help, "--n-cpu-moe") {
		cmd = append(cmd, "--n-cpu-moe", strconv.Itoa(moe))
	}
	// Thoughts go to their own field, not into the answer as <think> text.
	if nvHas(help, "--reasoning-format") {
		cmd = append(cmd, "--reasoning-format", "deepseek")
	}
	if budget, ok := state["reasoning_budget"]; ok && budget != nil && nvHas(help, "--reasoning-budget") {
		cmd = append(cmd, "--reasoning-budget", strconv.Itoa(anyInt(budget, -1)))
	}
	if n := whole("threads"); n > 0 {
		cmd = append(cmd, "--threads", strconv.Itoa(n))
	}
	if anyBoolDef(state["flash_attn"], true) {
		cmd = append(cmd, "-fa", "on")
	}
	if n := whole("batch_size"); n > 0 {
		cmd = append(cmd, "--batch-size", strconv.Itoa(n))
	}
	if n := whole("ubatch_size"); n > 0 {
		cmd = append(cmd, "--ubatch-size", strconv.Itoa(n))
	}
	if v := text("mmproj"); v != "" {
		cmd = append(cmd, "--mmproj", v)
	}
	if v := text("chat_template"); v != "" {
		cmd = append(cmd, "--chat-template", v)
	}
	if v, ok := state["temperature"]; ok && v != nil && num("temperature") >= 0 {
		cmd = append(cmd, "--temp", dec(num("temperature")))
	}
	if n := whole("top_k"); n > 0 {
		cmd = append(cmd, "--top-k", strconv.Itoa(n))
	}
	if v := num("top_p"); v > 0 && v <= 1 {
		cmd = append(cmd, "--top-p", dec(v))
	}
	if v, ok := state["min_p"]; ok && v != nil && num("min_p") >= 0 && num("min_p") < 1 {
		cmd = append(cmd, "--min-p", dec(num("min_p")))
	}
	if v := num("repeat_penalty"); v > 0 {
		cmd = append(cmd, "--repeat-penalty", dec(v))
	}
	if v, ok := state["repeat_last_n"]; ok && v != nil && whole("repeat_last_n") >= 0 {
		cmd = append(cmd, "--repeat-last-n", strconv.Itoa(whole("repeat_last_n")))
	}
	if n := anyInt(state["seed"], -1); n >= 0 {
		cmd = append(cmd, "--seed", strconv.Itoa(n))
	}
	if v := num("rope_freq_scale"); v > 0 {
		cmd = append(cmd, "--rope-freq-scale", dec(v))
	}
	if v := num("rope_freq_base"); v > 0 {
		cmd = append(cmd, "--rope-freq-base", dec(v))
	}
	if on("mlock") {
		cmd = append(cmd, "--mlock")
	}
	if on("no_mmap") {
		cmd = append(cmd, "--no-mmap")
	}
	if v := text("cache_type"); v != "" {
		cmd = append(cmd, "--cache-type-k", v, "--cache-type-v", v)
	}
	if on("no_kv_offload") {
		cmd = append(cmd, "--no-kv-offload")
	}
	if v := num("typical_p"); v > 0 && v <= 1 {
		cmd = append(cmd, "--typical", dec(v))
	}
	if v := num("tfs_z"); v > 0 && v <= 1 && nvHas(help, "--tfs") {
		cmd = append(cmd, "--tfs", dec(v))
	}
	if miro := whole("mirostat"); miro == 1 || miro == 2 {
		cmd = append(cmd, "--mirostat", strconv.Itoa(miro))
		if v := num("mirostat_tau"); v > 0 {
			cmd = append(cmd, "--mirostat-tau", dec(v))
		}
		if v := num("mirostat_eta"); v > 0 {
			cmd = append(cmd, "--mirostat-eta", dec(v))
		}
	}
	if v := num("presence_penalty"); v != 0 {
		cmd = append(cmd, "--presence-penalty", dec(v))
	}
	if v := num("frequency_penalty"); v != 0 {
		cmd = append(cmd, "--frequency-penalty", dec(v))
	}
	if v := num("dry_multiplier"); v != 0 {
		cmd = append(cmd, "--dry-multiplier", dec(v))
		if b := num("dry_base"); b > 0 {
			cmd = append(cmd, "--dry-base", dec(b))
		}
		if n := whole("dry_allowed_length"); n > 0 {
			cmd = append(cmd, "--dry-allowed-length", strconv.Itoa(n))
		}
		if n := whole("dry_penalty_last_n"); n != 0 {
			cmd = append(cmd, "--dry-penalty-last-n", strconv.Itoa(n))
		}
	}
	if v := num("xtc_probability"); v > 0 && v <= 1 {
		cmd = append(cmd, "--xtc-probability", dec(v))
		if t := num("xtc_threshold"); t > 0 && t < 1 {
			cmd = append(cmd, "--xtc-threshold", dec(t))
		}
	}
	if n := whole("main_gpu"); n > 0 {
		cmd = append(cmd, "--main-gpu", strconv.Itoa(n))
	}
	if n := whole("threads_batch"); n > 0 {
		cmd = append(cmd, "--threads-batch", strconv.Itoa(n))
	}
	if v := text("tensor_split"); v != "" {
		cmd = append(cmd, "--tensor-split", v)
	}
	if v := text("samplers"); v != "" {
		cmd = append(cmd, "--samplers", v)
	}
	if v := strings.ToLower(text("rope_scaling")); v == "linear" || v == "yarn" {
		cmd = append(cmd, "--rope-scaling", v)
		if n := whole("yarn_orig_ctx"); n > 0 {
			cmd = append(cmd, "--yarn-orig-ctx", strconv.Itoa(n))
		}
		if f := num("yarn_factor"); f > 0 {
			cmd = append(cmd, "--yarn-factor", dec(f))
		}
		if f := num("yarn_beta_fast"); f > 0 {
			cmd = append(cmd, "--yarn-beta-fast", dec(f))
		}
		if f := num("yarn_beta_slow"); f > 0 {
			cmd = append(cmd, "--yarn-beta-slow", dec(f))
		}
	}
	if n := whole("cache_reuse"); n > 0 && nvHas(help, "--cache-reuse") {
		cmd = append(cmd, "--cache-reuse", strconv.Itoa(n))
	}
	return cmd
}
