package httpapi

import (
	"context"
	"encoding/json"
	"io"
	"log"
	"net/http"
	"os"
	"path/filepath"
	"strconv"
	"strings"

	"github.com/AhmiDarrow/RemedyAI/native/go/secret"
)

const maxVoiceAudioBytes = 25 * 1024 * 1024

var knownKokoroVoices = []string{
	"af_heart", "af_bella", "af_nicole", "af_sarah", "af_sky",
	"am_adam", "am_michael", "am_fenrir",
	"bf_emma", "bf_isabella", "bm_george", "bm_lewis",
}

var validSTTModels = []string{
	"tiny", "base", "small", "medium", "large-v3", "large-v3-turbo",
}

var genderVoices = map[string]string{
	"female":  "af_heart",
	"male":    "am_michael",
	"neutral": "af_sky",
}

var smartTurnPin = struct {
	Repo, Revision, Filename, Licence string
}{
	Repo:     "pipecat-ai/smart-turn-v3",
	Revision: "f766f81d3cfdf7737ac64aad813d91bbfd56bf93",
	Filename: "smart-turn-v3.2-cpu.onnx",
	Licence:  "BSD-2-Clause",
}

// VoiceSpeakRequest is the JSON body for POST /api/voice/speak.
type VoiceSpeakRequest struct {
	Text  string   `json:"text"`
	Voice string   `json:"voice"`
	Speed *float64 `json:"speed"`
}

// VoiceSpeakResult is WAV audio from a VoiceWorker.
type VoiceSpeakResult struct {
	WAV        []byte
	SampleRate int
}

// VoiceTranscribeResult is local STT output.
type VoiceTranscribeResult struct {
	Text     string `json:"text"`
	Language string `json:"language,omitempty"`
}

// VoiceWorker is the clear Python-backed (or fixture) interface for ML ops.
// Nil on Server → speak/transcribe/install degrade with browser/none fallbacks.
type VoiceWorker interface {
	Speak(ctx context.Context, homeDir string, req VoiceSpeakRequest, gender string) (*VoiceSpeakResult, error)
	Transcribe(ctx context.Context, homeDir string, audio []byte, contentType, language string) (*VoiceTranscribeResult, error)
	Install(ctx context.Context, homeDir, component string) (started bool, err error)
}

func voiceHome(homeDir string) string {
	return filepath.Join(ResolveHomeDir(homeDir), "voice")
}

func voiceSettingsPath(homeDir string) string {
	return filepath.Join(voiceHome(homeDir), "voice.json")
}

func defaultVoiceSettings() map[string]any {
	return map[string]any{
		"tts_enabled":    true,
		"stt_enabled":    true,
		"speak_replies":  false,
		"voice_override": "",
		"speed":          1.0,
		"stt_model":      "small",
		"language":       "",
		"tts_quality":    "standard",
	}
}

func loadVoiceSettings(homeDir string) map[string]any {
	out := defaultVoiceSettings()
	raw, err := os.ReadFile(voiceSettingsPath(homeDir))
	if err != nil {
		return out
	}
	var parsed map[string]any
	if json.Unmarshal(raw, &parsed) != nil || parsed == nil {
		return out
	}
	for k := range out {
		if v, ok := parsed[k]; ok && v != nil {
			out[k] = v
		}
	}
	return normalizeVoiceSettings(out)
}

func normalizeVoiceSettings(cur map[string]any) map[string]any {
	speed := 1.0
	switch v := cur["speed"].(type) {
	case float64:
		speed = v
	case json.Number:
		if f, err := v.Float64(); err == nil {
			speed = f
		}
	}
	if speed < 0.5 {
		speed = 0.5
	}
	if speed > 2.0 {
		speed = 2.0
	}
	cur["speed"] = speed
	q := strings.ToLower(strings.TrimSpace(anyString(cur["tts_quality"])))
	if q == "hq" || q == "high" || q == "chatterbox" {
		cur["tts_quality"] = "hq"
	} else {
		cur["tts_quality"] = "standard"
	}
	cur["tts_enabled"] = anyBoolDef(cur["tts_enabled"], true)
	cur["stt_enabled"] = anyBoolDef(cur["stt_enabled"], true)
	cur["speak_replies"] = anyBoolDef(cur["speak_replies"], false)
	cur["voice_override"] = anyString(cur["voice_override"])
	cur["language"] = anyString(cur["language"])
	model := strings.ToLower(strings.TrimSpace(anyString(cur["stt_model"])))
	okModel := false
	for _, m := range validSTTModels {
		if model == m {
			okModel = true
			break
		}
	}
	if !okModel {
		model = "small"
	}
	cur["stt_model"] = model
	return cur
}

func saveVoiceSettings(homeDir string, patch map[string]any) (map[string]any, error) {
	cur := loadVoiceSettings(homeDir)
	defaults := defaultVoiceSettings()
	for k, v := range patch {
		if _, ok := defaults[k]; !ok || v == nil {
			continue
		}
		cur[k] = v
	}
	if _, ok := patch["tts_quality"]; ok {
		q := strings.ToLower(strings.TrimSpace(anyString(cur["tts_quality"])))
		if q == "hq" || q == "high" || q == "chatterbox" {
			cur["tts_quality"] = "hq"
			cur["tts_enabled"] = true
		}
	}
	cur = normalizeVoiceSettings(cur)
	if err := os.MkdirAll(voiceHome(homeDir), 0o700); err != nil {
		return nil, err
	}
	data, err := json.MarshalIndent(cur, "", "  ")
	if err != nil {
		return nil, err
	}
	data = append(data, '\n')
	if err := secret.WriteFileAtomic(voiceSettingsPath(homeDir), data, 0o600); err != nil {
		return nil, err
	}
	return cur, nil
}

func ttsInstalled(homeDir string) bool {
	base := filepath.Join(voiceHome(homeDir), "tts")
	model := filepath.Join(base, "kokoro-v1.0.onnx")
	voices := filepath.Join(base, "voices-v1.0.bin")
	return fileExists(model) && fileExists(voices)
}

func sttInstalled(homeDir string) bool {
	// faster-whisper caches under voice/stt; any file there counts as present.
	dir := filepath.Join(voiceHome(homeDir), "stt")
	ents, err := os.ReadDir(dir)
	return err == nil && len(ents) > 0
}

func smartTurnInstalled(homeDir string) bool {
	p := filepath.Join(voiceHome(homeDir), "models", "smart-turn", smartTurnPin.Filename)
	return fileExists(p)
}

func chatterboxInstalled(homeDir string) bool {
	p := filepath.Join(voiceHome(homeDir), "chatterbox", "ready.json")
	raw, err := os.ReadFile(p)
	if err != nil {
		return false
	}
	var parsed map[string]any
	if json.Unmarshal(raw, &parsed) != nil {
		return false
	}
	return anyBoolDef(parsed["ok"], false)
}

func voiceForGender(gender, override string) string {
	if ov := strings.ToLower(strings.TrimSpace(override)); ov != "" {
		return ov
	}
	g := strings.ToLower(strings.TrimSpace(gender))
	if v, ok := genderVoices[g]; ok {
		return v
	}
	return genderVoices["female"]
}

func agentGenderFromConfig(cfg ConfigMap) string {
	g := strings.ToLower(strings.TrimSpace(cfgString(cfg, "agent_gender", "female")))
	switch g {
	case "male", "female", "neutral":
		return g
	default:
		return "female"
	}
}

func (s *Server) voiceStatusPayload() map[string]any {
	home := ResolveHomeDir(s.homeDir)
	cfg := LoadConfig(s.homeDir)
	settings := loadVoiceSettings(home)
	gender := agentGenderFromConfig(cfg)
	ttsOK := ttsInstalled(home)
	sttOK := sttInstalled(home)
	turnOK := smartTurnInstalled(home)
	hqInstalled := chatterboxInstalled(home)
	quality := anyString(settings["tts_quality"])
	usingHQ := quality == "hq" && hqInstalled

	reasonTTS, hintTTS := voiceUnavailable(ttsOK || usingHQ, ttsOK || usingHQ,
		"Remedy's speaking voice is not on this computer yet.")
	reasonSTT, hintSTT := voiceUnavailable(sttOK, sttOK, "Hearing is not ready yet.")
	reasonTurn, hintTurn := voiceUnavailable(turnOK, turnOK, "Turn-taking is not on this computer yet.")

	var hqReason any
	var hqHint any
	if !hqInstalled {
		hqReason = "Remedy's voice is still arriving."
	}

	var turnPath any
	if turnOK {
		turnPath = filepath.Join(voiceHome(home), "models", "smart-turn", smartTurnPin.Filename)
	}

	var ttsEngine any
	if usingHQ {
		ttsEngine = "chatterbox"
	} else if ttsOK {
		ttsEngine = "kokoro-82m"
	}
	var sttEngine any
	if sttOK {
		sttEngine = "faster-whisper"
	}
	var turnEngine any
	if turnOK {
		turnEngine = "smart-turn-v3"
	}
	var hqEngine any
	if hqInstalled {
		hqEngine = "chatterbox"
	}

	return map[string]any{
		"tts": map[string]any{
			"available": ttsOK || usingHQ,
			"enabled":   anyBoolDef(settings["tts_enabled"], true),
			"engine":    ttsEngine,
			"quality":   quality,
			"reason":    reasonTTS,
			"hint":      hintTTS,
			"deps":      ttsOK || usingHQ,
			"installed": ttsOK,
			"install":   nil,
			"voice":     voiceForGender(gender, anyString(settings["voice_override"])),
			"voices":    append([]string(nil), knownKokoroVoices...),
			"fallback":  "browser",
		},
		"stt": map[string]any{
			"available": sttOK,
			"enabled":   anyBoolDef(settings["stt_enabled"], true),
			"engine":    sttEngine,
			"reason":    reasonSTT,
			"hint":      hintSTT,
			"deps":      sttOK,
			"model":     settings["stt_model"],
			"models":    append([]string(nil), validSTTModels...),
			"installed": sttOK,
			"install":   nil,
		},
		"smart_turn": map[string]any{
			"available": turnOK,
			"engine":    turnEngine,
			"reason":    reasonTurn,
			"hint":      hintTurn,
			"deps":      turnOK,
			"installed": turnOK,
			"install":   nil,
			"path":      turnPath,
			"source": map[string]any{
				"repo":     smartTurnPin.Repo,
				"revision": smartTurnPin.Revision,
				"filename": smartTurnPin.Filename,
				"licence":  smartTurnPin.Licence,
			},
			"fallback": "energy",
		},
		"pack": map[string]any{
			"deps":      ttsOK && sttOK,
			"install":   nil,
			"supported": true,
		},
		"hq": map[string]any{
			"available": hqInstalled,
			"engine":    hqEngine,
			"deps":      hqInstalled,
			"installed": hqInstalled,
			"install":   nil,
			"reason":    hqReason,
			"hint":      hqHint,
			"approx_mb": 1100,
			"licence":   "MIT",
			"source":    "Resemble AI",
			"fallback":  "kokoro",
		},
		"settings": settings,
	}
}

func voiceUnavailable(ok, deps bool, missing string) (any, any) {
	if ok {
		return nil, nil
	}
	if !deps {
		return missing, nil
	}
	return missing, nil
}

func (s *Server) handleVoiceStatus(w http.ResponseWriter, _ *http.Request) {
	writeJSON(w, http.StatusOK, s.voiceStatusPayload())
}

func (s *Server) handleVoiceSettings(w http.ResponseWriter, r *http.Request) {
	var patch map[string]any
	dec := json.NewDecoder(r.Body)
	dec.UseNumber()
	if err := dec.Decode(&patch); err != nil && err != io.EOF {
		writeJSON(w, http.StatusBadRequest, map[string]string{"detail": "invalid JSON body"})
		return
	}
	saved, err := saveVoiceSettings(s.homeDir, patch)
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]string{"detail": err.Error()})
		return
	}
	if anyString(saved["tts_quality"]) == "hq" && s.voice != nil {
		_, _ = s.voice.Install(r.Context(), ResolveHomeDir(s.homeDir), "chatterbox")
	}
	writeJSON(w, http.StatusOK, saved)
}

func (s *Server) handleVoiceInstall(w http.ResponseWriter, r *http.Request) {
	var body struct {
		Component string `json:"component"`
	}
	_ = json.NewDecoder(r.Body).Decode(&body)
	comp := strings.ToLower(strings.TrimSpace(body.Component))
	if comp == "" {
		comp = "tts"
	}
	switch comp {
	case "tts", "stt", "smart-turn", "smart_turn", "chatterbox", "hq", "all", "*", "pack", "voice":
	default:
		writeJSON(w, http.StatusOK, map[string]any{
			"ok":    false,
			"error": "Unknown voice piece '" + comp + "'.",
		})
		return
	}
	if s.voice == nil {
		writeJSON(w, http.StatusOK, map[string]any{
			"ok":    false,
			"error": "Voice worker is not attached to remedy-runtime.",
			"hint":  "Wire a VoiceWorker (Python speech lane) or use the Python sidecar.",
		})
		return
	}
	started, err := s.voice.Install(r.Context(), ResolveHomeDir(s.homeDir), comp)
	if err != nil {
		writeJSON(w, http.StatusOK, map[string]any{"ok": false, "error": err.Error()})
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"ok": true, "started": started})
}

func (s *Server) handleVoiceClientLog(w http.ResponseWriter, r *http.Request) {
	var body struct {
		Message string `json:"message"`
	}
	_ = json.NewDecoder(r.Body).Decode(&body)
	msg := body.Message
	if len(msg) > 400 {
		msg = msg[:400]
	}
	log.Printf("voice client: %s", msg)
	writeJSON(w, http.StatusOK, map[string]any{"ok": true})
}

func (s *Server) handleVoiceSpeak(w http.ResponseWriter, r *http.Request) {
	home := ResolveHomeDir(s.homeDir)
	settings := loadVoiceSettings(home)
	if !anyBoolDef(settings["tts_enabled"], true) {
		writeJSON(w, http.StatusServiceUnavailable, map[string]string{
			"error": "tts disabled", "fallback": "browser",
		})
		return
	}
	var req VoiceSpeakRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"detail": "invalid JSON body"})
		return
	}
	if s.voice == nil {
		writeJSON(w, http.StatusServiceUnavailable, map[string]string{
			"error": "local TTS unavailable", "fallback": "browser",
		})
		return
	}
	gender := agentGenderFromConfig(LoadConfig(s.homeDir))
	out, err := s.voice.Speak(r.Context(), home, req, gender)
	if err != nil || out == nil || len(out.WAV) == 0 {
		writeJSON(w, http.StatusServiceUnavailable, map[string]string{
			"error": "local TTS unavailable", "fallback": "browser",
		})
		return
	}
	sr := out.SampleRate
	if sr <= 0 {
		sr = 24000
	}
	w.Header().Set("Content-Type", "audio/wav")
	w.Header().Set("X-Sample-Rate", strconv.Itoa(sr))
	w.Header().Set("Cache-Control", "no-store")
	w.WriteHeader(http.StatusOK)
	_, _ = w.Write(out.WAV)
}

func (s *Server) handleVoiceTranscribe(w http.ResponseWriter, r *http.Request) {
	home := ResolveHomeDir(s.homeDir)
	if !anyBoolDef(loadVoiceSettings(home)["stt_enabled"], true) {
		writeJSON(w, http.StatusServiceUnavailable, map[string]string{"error": "stt disabled"})
		return
	}
	body, err := io.ReadAll(io.LimitReader(r.Body, maxVoiceAudioBytes+1))
	if err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "empty audio"})
		return
	}
	if len(body) == 0 {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "empty audio"})
		return
	}
	if len(body) > maxVoiceAudioBytes {
		writeJSON(w, http.StatusRequestEntityTooLarge, map[string]string{"error": "audio too large"})
		return
	}
	if s.voice == nil {
		writeJSON(w, http.StatusServiceUnavailable, map[string]string{
			"error":    "Hearing is not ready yet. Open Settings → Voice to download it.",
			"fallback": "none",
		})
		return
	}
	lang := strings.TrimSpace(r.URL.Query().Get("language"))
	out, err := s.voice.Transcribe(r.Context(), home, body, r.Header.Get("Content-Type"), lang)
	if err != nil || out == nil || strings.TrimSpace(out.Text) == "" && err != nil {
		writeJSON(w, http.StatusServiceUnavailable, map[string]string{
			"error":    "Hearing is not ready yet. Open Settings → Voice to download it.",
			"fallback": "none",
		})
		return
	}
	if out == nil {
		writeJSON(w, http.StatusServiceUnavailable, map[string]string{
			"error":    "Hearing is not ready yet. Open Settings → Voice to download it.",
			"fallback": "none",
		})
		return
	}
	payload := map[string]any{"text": out.Text}
	if out.Language != "" {
		payload["language"] = out.Language
	}
	writeJSON(w, http.StatusOK, payload)
}

func anyString(v any) string {
	switch t := v.(type) {
	case string:
		return t
	case json.Number:
		return t.String()
	default:
		return ""
	}
}

func anyBoolDef(v any, def bool) bool {
	if v == nil {
		return def
	}
	switch t := v.(type) {
	case bool:
		return t
	case string:
		switch strings.ToLower(strings.TrimSpace(t)) {
		case "1", "true", "yes", "on":
			return true
		case "0", "false", "no", "off":
			return false
		default:
			return def
		}
	case float64:
		return t != 0
	case json.Number:
		f, err := t.Float64()
		if err == nil {
			return f != 0
		}
	}
	return def
}
