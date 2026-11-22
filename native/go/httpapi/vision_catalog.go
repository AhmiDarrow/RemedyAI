package httpapi

import (
	"runtime"
	"strings"
)

// Vision catalog constants — parity with remedy.runtime.catalog (Qwen3.5-4B mind).

const (
	visionDefaultModelID   = "qwen35-4b-mind"
	visionDefaultHost      = "127.0.0.1"
	visionDefaultPort      = 8740
	visionLlamaCPPTag      = "b12628"
	visionBundlePolicy     = "cpu_and_cuda"
	visionDefaultRuntimeID = "win-cpu-x64"
)

var visionLocalRoles = []string{"vision", "nano", "helper"}

type visionModelSpec struct {
	ID                    string
	Name                  string
	HFRepo                string
	ModelFile             string
	MMProjFile            string
	ModelSHA256           string
	MMProjSHA256          string
	ModelBytes            int64
	MMProjBytes           int64
	FallbackModelFile     string
	FallbackSHA256        string
	FallbackBytes         int64
	MinRAMGB              int
	License               string
	Notes                 string
	HasMTP                bool
	Quant                 string
}

func (m visionModelSpec) approxDownloadBytes() int64 {
	return m.ModelBytes + m.MMProjBytes
}

func (m visionModelSpec) approxFallbackDownloadBytes() int64 {
	if m.FallbackModelFile == "" {
		return m.approxDownloadBytes()
	}
	return m.FallbackBytes + m.MMProjBytes
}

func (m visionModelSpec) toPublic() map[string]any {
	bytes := m.approxDownloadBytes()
	sha := m.ModelSHA256
	if sha == "" {
		sha = "TODO"
	}
	fbSHA := m.FallbackSHA256
	if fbSHA == "" {
		fbSHA = "TODO"
	}
	return map[string]any{
		"id":                    m.ID,
		"name":                  m.Name,
		"hf_repo":               m.HFRepo,
		"model_file":            m.ModelFile,
		"mmproj_file":           m.MMProjFile,
		"model_sha256":          sha,
		"mmproj_sha256":         m.MMProjSHA256,
		"fallback_model_file":   m.FallbackModelFile,
		"fallback_sha256":       fbSHA,
		"fallback_bytes":        m.FallbackBytes,
		"approx_download_bytes": bytes,
		"approx_download_gb":    float64(int(float64(bytes)/(1024*1024*1024)*100+0.5)) / 100,
		"approx_fallback_download_bytes": m.approxFallbackDownloadBytes(),
		"min_ram_gb":            m.MinRAMGB,
		"license":               m.License,
		"notes":                 m.Notes,
		"is_default":            m.ID == visionDefaultModelID,
		"bundled":               true,
		"roles":                 append([]string(nil), visionLocalRoles...),
		"has_mtp":               m.HasMTP,
		"quant":                 m.Quant,
		"required":              true,
	}
}

var visionModels = []visionModelSpec{
	{
		ID: visionDefaultModelID, Name: "Qwen3.5 4B mind (Q6_K)",
		HFRepo: "unsloth/Qwen3.5-4B-GGUF",
		ModelFile: "Qwen3.5-4B-Q6_K.gguf",
		MMProjFile: "mmproj-F16.gguf",
		ModelSHA256: "TODO",
		MMProjSHA256: "cd88edcf8d031894960bb0c9c5b9b7e1fea6ebee02b9f7ce925a00d12891f864",
		ModelBytes: 3_525_956_768, MMProjBytes: 672_423_616,
		FallbackModelFile: "Qwen3.5-4B-Q4_K_M.gguf",
		FallbackSHA256: "TODO",
		FallbackBytes: 2_740_937_888,
		MinRAMGB: 8, License: "Apache-2.0",
		HasMTP: true, Quant: "Q6_K",
		Notes: "Required local mind (Apache 2.0) — vision, nano, helper, dreams, " +
			"workers, and media digests on one llama-server. Q6_K default, " +
			"Q4_K_M autofit fallback, official mmproj-F16. First-run download; " +
			"language-model sha256 is TODO until the fine-tuned GGUF ships.",
	},
}

type visionRuntimeSpec struct {
	ID       string
	Platform string
	Tag      string
	ZipName  string
	SizeBytes int64
	Bundled  bool
}

var visionRuntimes = []visionRuntimeSpec{
	{ID: "win-cpu-x64", Platform: "win-cpu-x64", Tag: visionLlamaCPPTag,
		ZipName: "llama-b12628-bin-win-cpu-x64.zip", SizeBytes: 25_037_881, Bundled: true},
	{ID: "win-cuda-12.4-x64", Platform: "win-cuda-12.4-x64", Tag: visionLlamaCPPTag,
		ZipName: "llama-b12628-bin-win-cuda-12.4-x64.zip", SizeBytes: 357_973_598, Bundled: true},
	{ID: "linux-cpu-x64", Platform: "linux-cpu-x64", Tag: visionLlamaCPPTag,
		ZipName: "llama-b12628-bin-ubuntu-x64.tar.gz", SizeBytes: 23_225_939},
	{ID: "linux-vulkan-x64", Platform: "linux-vulkan-x64", Tag: visionLlamaCPPTag,
		ZipName: "llama-b12628-bin-ubuntu-vulkan-x64.tar.gz", SizeBytes: 36_908_581},
}

func visionModelByID(id string) visionModelSpec {
	id = strings.ToLower(strings.TrimSpace(id))
	if id == "" || id == "smolvlm2-2.2b" || id == "qwen2.5-vl-3b" {
		id = visionDefaultModelID
	}
	for _, m := range visionModels {
		if m.ID == id {
			return m
		}
	}
	return visionModels[0]
}

func visionDefaultRuntime(preferGPU bool) string {
	switch runtime.GOOS {
	case "windows":
		if preferGPU {
			return "win-cuda-12.4-x64"
		}
		return "win-cpu-x64"
	case "linux":
		if preferGPU {
			return "linux-vulkan-x64"
		}
		return "linux-cpu-x64"
	default:
		return visionDefaultRuntimeID
	}
}

func visionHostRuntimeIDs() []string {
	switch runtime.GOOS {
	case "windows":
		return []string{"win-cpu-x64", "win-cuda-12.4-x64"}
	case "linux":
		return []string{"linux-cpu-x64", "linux-vulkan-x64"}
	default:
		return []string{visionDefaultRuntimeID}
	}
}

func visionCatalogPublic() map[string]any {
	models := make([]map[string]any, 0, len(visionModels))
	for _, m := range visionModels {
		models = append(models, m.toPublic())
	}
	runtimes := make([]map[string]any, 0, len(visionRuntimes))
	for _, r := range visionRuntimes {
		runtimes = append(runtimes, map[string]any{
			"id": r.ID, "platform": r.Platform, "tag": r.Tag,
			"zip_name": r.ZipName, "size_bytes": r.SizeBytes, "bundled": r.Bundled,
		})
	}
	return map[string]any{
		"default_model_id":       visionDefaultModelID,
		"default_local_model_id": visionDefaultModelID,
		"models":                 models,
		"roles":                  append([]string(nil), visionLocalRoles...),
		"bundled_runtime_ids":    []string{"win-cpu-x64", "win-cuda-12.4-x64"},
		"host_runtime_ids":       visionHostRuntimeIDs(),
		"default_runtime_id":     visionDefaultRuntime(false),
		"bundle_policy":          visionBundlePolicy,
		"llama_cpp_tag":          visionLlamaCPPTag,
		"default_port":           visionDefaultPort,
		"required":               true,
		"runtimes":               runtimes,
	}
}
