package httpapi

import (
	"slices"
	"strings"
	"testing"
)

func TestRmbCommandCarriesTheSavedSettings(t *testing.T) {
	state := mergeRmbState(map[string]any{
		"ctx_size": 16384, "n_gpu_layers": 20, "threads": 6, "thinking": "off",
		"temperature": 0.7, "seed": 42, "mlock": true, "rope_scaling": "yarn", "yarn_orig_ctx": 8192,
	})
	cmd := rmbCommand("llama-server", "model.gguf", "127.0.0.1", 8787, state, nil)
	line := strings.Join(cmd, " ")
	for _, want := range []string{
		"llama-server -m model.gguf --host 127.0.0.1 --port 8787 --ctx-size 16384 -ngl 20",
		"--threads 6", "--reasoning off", "--temp 0.7", "--seed 42", "--mlock", "--jinja",
		"--rope-scaling yarn --yarn-orig-ctx 8192", "--reasoning-format deepseek",
	} {
		if !strings.Contains(line, want) {
			t.Fatalf("missing %q in: %s", want, line)
		}
	}
	// Unset figures are not sent, so llama-server keeps its own defaults.
	for _, absent := range []string{"--mirostat", "--dry-multiplier", "--xtc-probability", "--tensor-split", "--no-mmap"} {
		if slices.Contains(cmd, absent) {
			t.Fatalf("%s was sent though nothing set it: %s", absent, line)
		}
	}
}

func TestRmbCommandLeavesOutWhatTheBuildLacksAndAutoLayers(t *testing.T) {
	help := []byte("usage: llama-server --ctx-size N --jinja --parallel N")
	cmd := rmbCommand("llama-server", "m.gguf", "127.0.0.1", 8787, mergeRmbState(map[string]any{"thinking": "off"}), help)
	for _, absent := range []string{"--reasoning", "--reasoning-format", "--reasoning-budget", "--cache-reuse", "-ngl"} {
		if slices.Contains(cmd, absent) {
			t.Fatalf("%s sent: %v", absent, cmd)
		}
	}
}
