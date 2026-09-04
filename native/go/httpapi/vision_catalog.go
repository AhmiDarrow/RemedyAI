package httpapi

import (
	"runtime"
	"strings"
)

// Vision catalog constants — parity with remedy.runtime.catalog (SmolVLM2 pin).

const (
	visionDefaultModelID   = "smolvlm2-2.2b"
	visionDefaultHost      = "127.0.0.1"
	visionDefaultPort      = 8740
	visionLlamaCPPTag      = "b10107"
	visionBundlePolicy     = "cpu_and_cuda"
	visionDefaultRuntimeID = "win-cpu-x64"
)

var visionLocalRoles = []string{"vision", "nano", "helper"}

type visionModelSpec struct {
	ID            string
	Name          string
	HFRepo        string
	ModelFile     string
	MMProjFile    string
	ModelBytes    int64
	MMProjBytes   int64
	MinRAMGB      int
	License       string
	Notes         string
}

func (m visionModelSpec) approxDownloadBytes() int64 {
	return m.ModelBytes + m.MMProjBytes
}

func (m visionModelSpec) toPublic() map[string]any {
	bytes := m.approxDownloadBytes()
	return map[string]any{
		"id":                    m.ID,
		"name":                  m.Name,
		"hf_repo":               m.HFRepo,
		"model_file":            m.ModelFile,
		"mmproj_file":           m.MMProjFile,
		"approx_download_bytes": bytes,
		"approx_download_gb":    float64(int(float64(bytes)/(1024*1024*1024)*100+0.5)) / 100,
		"min_ram_gb":            m.MinRAMGB,
		"license":               m.License,
		"notes":                 m.Notes,
		"is_default":            m.ID == visionDefaultModelID,
		"bundled":               true,
		"roles":                 append([]string(nil), visionLocalRoles...),
	}
}

var visionModels = []visionModelSpec{
	{
		ID: visionDefaultModelID, Name: "SmolVLM2 2.2B",
		HFRepo: "ggml-org/SmolVLM2-2.2B-Instruct-GGUF",
		ModelFile: "SmolVLM2-2.2B-Instruct-Q4_K_M.gguf",
		MMProjFile: "mmproj-SmolVLM2-2.2B-Instruct-Q8_0.gguf",
		ModelBytes: 1_112_602_656, MMProjBytes: 592_523_200,
		MinRAMGB: 4, License: "Apache-2.0",
		Notes: "Required local model (Apache 2.0) — vision, nano swarm, helper. " +
			"First-run download; same weights on every PC for a given Remedy release.",
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
		ZipName: "llama-b10107-bin-win-cpu-x64.zip", SizeBytes: 18_213_827, Bundled: true},
	{ID: "win-cuda-12.4-x64", Platform: "win-cuda-12.4-x64", Tag: visionLlamaCPPTag,
		ZipName: "llama-b10107-bin-win-cuda-12.4-x64.zip", SizeBytes: 247_064_556, Bundled: true},
	{ID: "linux-cpu-x64", Platform: "linux-cpu-x64", Tag: visionLlamaCPPTag,
		ZipName: "llama-b10107-bin-ubuntu-x64.tar.gz", SizeBytes: 16_275_561},
	{ID: "linux-vulkan-x64", Platform: "linux-vulkan-x64", Tag: visionLlamaCPPTag,
		ZipName: "llama-b10107-bin-ubuntu-vulkan-x64.tar.gz", SizeBytes: 32_239_108},
}

func visionModelByID(id string) visionModelSpec {
	id = strings.ToLower(strings.TrimSpace(id))
	if id == "" {
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
		"runtimes":               runtimes,
	}
}
