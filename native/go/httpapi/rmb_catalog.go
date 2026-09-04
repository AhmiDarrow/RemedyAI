package httpapi

import (
	"path/filepath"
	"strings"
)

// RMB chat model catalog — coding / tool-use GGUFs (Python catalog.py parity).

type rmbModelSpec struct {
	ID            string
	Name          string
	Filename      string
	HFRepo        string
	ApproxGB      float64
	NCtxRecommend int
	Notes         string
	SizeLabel     string
	NCPUMoE       int
	ActiveB       string
}

func (m rmbModelSpec) toPublic() map[string]any {
	return map[string]any{
		"id":              m.ID,
		"name":            m.Name,
		"filename":        m.Filename,
		"hf_repo":         m.HFRepo,
		"approx_gb":       m.ApproxGB,
		"n_ctx_recommend": m.NCtxRecommend,
		"notes":           m.Notes,
		"size_label":      m.SizeLabel,
		"n_cpu_moe":       m.NCPUMoE,
		"active_b":        m.ActiveB,
	}
}

const defaultRMBModelID = "qwen35-9b"

var rmbModels = []rmbModelSpec{
	{
		ID: "qwen35-9b", Name: "Qwen3.5 9B (Q6_K)",
		Filename: "Qwen3.5-9B-Q6_K.gguf", HFRepo: "unsloth/Qwen3.5-9B-GGUF",
		ApproxGB: 7.5, NCtxRecommend: 16384, SizeLabel: "9b",
		Notes: "Default RMB chat model - fits a 12GB card whole at 6-bit, so it " +
			"keeps tool-call structure a 4-bit model loses. Fastest of the " +
			"measured options and the strongest on the agent suite.",
	},
	{
		ID: "qwen36-35b-a3b", Name: "Qwen3.6 35B-A3B (Q4_K_XL)",
		Filename: "Qwen3.6-35B-A3B-UD-Q4_K_XL.gguf", HFRepo: "unsloth/Qwen3.6-35B-A3B-GGUF",
		ApproxGB: 20.8, NCtxRecommend: 16384, SizeLabel: "35b", NCPUMoE: 99, ActiveB: "3b",
		Notes: "MoE with 3B active parameters; experts run on the CPU, so it " +
			"needs only ~4GB VRAM and ~21GB RAM. Slower per step than the 9B " +
			"(prefill cost) and degrades when the CPU is busy - pick it when " +
			"VRAM is scarce or another model needs the GPU.",
	},
	{
		ID: "qwen25-coder-7b", Name: "Qwen2.5 Coder 7B (Q4_K_M)",
		Filename: "Qwen2.5-Coder-7B-Instruct-Q4_K_M.gguf", HFRepo: "Qwen/Qwen2.5-Coder-7B-Instruct-GGUF",
		ApproxGB: 4.7, NCtxRecommend: 8192, SizeLabel: "7b",
		Notes: "Default RMB chat model — strong coding + tools. " +
			"Place the GGUF under ~/.remedy/rmb/models/ or point Settings at a path.",
	},
	{
		ID: "qwen25-coder-14b", Name: "Qwen2.5 Coder 14B (Q4_K_M)",
		Filename: "Qwen2.5-Coder-14B-Instruct-Q4_K_M.gguf", HFRepo: "Qwen/Qwen2.5-Coder-14B-Instruct-GGUF",
		ApproxGB: 9.0, NCtxRecommend: 8192, SizeLabel: "14b",
		Notes: "Higher quality when VRAM allows (~10GB+ free for weights).",
	},
	{
		ID: "qwen25-7b", Name: "Qwen2.5 Instruct 7B (Q4_K_M)",
		Filename: "Qwen2.5-7B-Instruct-Q4_K_M.gguf", HFRepo: "Qwen/Qwen2.5-7B-Instruct-GGUF",
		ApproxGB: 4.7, NCtxRecommend: 8192, SizeLabel: "7b",
		Notes: "General instruct; prefer Coder for agent tool chains.",
	},
}

var rmbProfiles = map[string]map[string]any{
	"autofit": {
		"label": "Autofit (recommended)", "ctx_size": 0, "n_gpu_layers": -1,
		"blurb": "Largest stable context that fits this GPU/RAM. Default.",
	},
	"agent": {
		"label": "Agent", "ctx_size": 8192, "n_gpu_layers": -1,
		"blurb": "Fixed 8k window — long tool chains on 12GB class GPUs.",
	},
	"turbo": {
		"label": "Turbo", "ctx_size": 4096, "n_gpu_layers": -1,
		"blurb": "Shorter context, snappier turns.",
	},
	"quality": {
		"label": "Quality", "ctx_size": 16384, "n_gpu_layers": -1,
		"blurb": "Fixed 16k window (may OOM on small GPUs — prefer Autofit).",
	},
}

func rmbCatalogPublic() map[string]any {
	models := make([]map[string]any, 0, len(rmbModels))
	for _, m := range rmbModels {
		models = append(models, m.toPublic())
	}
	profiles := map[string]any{}
	for k, v := range rmbProfiles {
		row := map[string]any{"id": k}
		for kk, vv := range v {
			row[kk] = vv
		}
		profiles[k] = row
	}
	return map[string]any{
		"default_model_id": defaultRMBModelID,
		"models":           models,
		"profiles":         profiles,
		"brand":            "RMB",
		"brand_full":       "Remedy Muscle Bridge",
		"engine":           "llama.cpp (llama-server)",
		"default_port":     8787,
		"note": "RMB is Remedy's local agent host powered by llama.cpp. " +
			"The retired custom RMB4 format is not used.",
	}
}

func rmbModelByID(id string) rmbModelSpec {
	want := strings.TrimSpace(id)
	if want == "" {
		want = defaultRMBModelID
	}
	for _, m := range rmbModels {
		if m.ID == want {
			return m
		}
	}
	for _, m := range rmbModels {
		if m.ID == defaultRMBModelID {
			return m
		}
	}
	return rmbModels[0]
}

func catalogIDFromHint(hint string) string {
	raw := strings.TrimSpace(hint)
	if raw == "" {
		return ""
	}
	raw = strings.ReplaceAll(raw, `\`, "/")
	h := strings.ToLower(filepath.Base(raw))
	h = strings.TrimSuffix(h, ".gguf")
	for _, m := range rmbModels {
		if h == m.ID {
			return m.ID
		}
		stem := strings.ToLower(strings.TrimSuffix(m.Filename, ".gguf"))
		if h == stem || h == strings.ToLower(m.Filename) {
			return m.ID
		}
	}
	return ""
}
