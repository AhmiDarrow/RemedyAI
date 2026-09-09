package httpapi

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"strings"

	"github.com/AhmiDarrow/RemedyAI/native/go/tools"
)

// RMDYVoiceWorker implements VoiceWorker over the supervised RMDY Python worker.
type RMDYVoiceWorker struct {
	exec *tools.RMDYExecutor
}

// RMDYVisionWorker implements VisionWorker over the supervised RMDY Python worker.
type RMDYVisionWorker struct {
	exec *tools.RMDYExecutor
}

// NewRMDYVoiceWorker binds speak/transcribe/install to an attached FrameCaller.
// Nil caller → error (fail closed; never return a soft no-op worker).
func NewRMDYVoiceWorker(caller tools.FrameCaller) (*RMDYVoiceWorker, error) {
	if caller == nil {
		return nil, errors.New("voice worker requires an attached RMDY frame caller")
	}
	return &RMDYVoiceWorker{exec: tools.NewRMDYExecutor(caller)}, nil
}

// NewRMDYVisionWorker binds vision mutate/start/stop to an attached FrameCaller.
func NewRMDYVisionWorker(caller tools.FrameCaller) (*RMDYVisionWorker, error) {
	if caller == nil {
		return nil, errors.New("vision worker requires an attached RMDY frame caller")
	}
	return &RMDYVisionWorker{exec: tools.NewRMDYExecutor(caller)}, nil
}

// AttachMLWorkers builds non-nil VoiceWorker and VisionWorker from one RMDY caller.
func AttachMLWorkers(caller tools.FrameCaller) (VoiceWorker, VisionWorker, error) {
	voice, err := NewRMDYVoiceWorker(caller)
	if err != nil {
		return nil, nil, err
	}
	vision, err := NewRMDYVisionWorker(caller)
	if err != nil {
		return nil, nil, err
	}
	return voice, vision, nil
}

func (w *RMDYVoiceWorker) call(ctx context.Context, toolID string, input any) (map[string]any, error) {
	if w == nil || w.exec == nil {
		return nil, errors.New("voice worker is not attached")
	}
	raw, err := json.Marshal(input)
	if err != nil {
		return nil, err
	}
	res, err := w.exec.Execute(ctx, tools.Request{ToolID: toolID, Version: 1, Input: raw})
	if err != nil {
		return nil, err
	}
	var out map[string]any
	if err := json.Unmarshal(res.Output, &out); err != nil {
		return nil, fmt.Errorf("%s decode: %w", toolID, err)
	}
	if out == nil {
		return nil, fmt.Errorf("%s returned empty output", toolID)
	}
	return out, nil
}

func (w *RMDYVisionWorker) call(ctx context.Context, toolID string, input any) (map[string]any, error) {
	if w == nil || w.exec == nil {
		return nil, errors.New("vision worker is not attached")
	}
	raw, err := json.Marshal(input)
	if err != nil {
		return nil, err
	}
	res, err := w.exec.Execute(ctx, tools.Request{ToolID: toolID, Version: 1, Input: raw})
	if err != nil {
		return nil, err
	}
	var out map[string]any
	if err := json.Unmarshal(res.Output, &out); err != nil {
		return nil, fmt.Errorf("%s decode: %w", toolID, err)
	}
	if out == nil {
		return nil, fmt.Errorf("%s returned empty output", toolID)
	}
	return out, nil
}

func (w *RMDYVoiceWorker) Speak(ctx context.Context, homeDir string, req VoiceSpeakRequest, gender string) (*VoiceSpeakResult, error) {
	in := map[string]any{
		"home_dir":         homeDir,
		tools.GoBoundField: true,
		"text":             req.Text,
		"gender":           gender,
	}
	if strings.TrimSpace(req.Voice) != "" {
		in["voice"] = req.Voice
	}
	if req.Speed != nil {
		in["speed"] = *req.Speed
	}
	out, err := w.call(ctx, "voice.speak", in)
	if err != nil {
		return nil, err
	}
	if anyBoolDef(out["unavailable"], false) {
		return nil, nil
	}
	b64 := anyString(out["wav_b64"])
	if b64 == "" {
		return nil, nil
	}
	wav, err := base64.StdEncoding.DecodeString(b64)
	if err != nil {
		return nil, fmt.Errorf("voice.speak wav: %w", err)
	}
	sr := anyInt(out["sample_rate"], 24000)
	return &VoiceSpeakResult{WAV: wav, SampleRate: sr}, nil
}

func (w *RMDYVoiceWorker) Transcribe(ctx context.Context, homeDir string, audio []byte, contentType, language string) (*VoiceTranscribeResult, error) {
	suffix := suffixFromContentType(contentType)
	in := map[string]any{
		"home_dir":         homeDir,
		tools.GoBoundField: true,
		"audio_b64":        base64.StdEncoding.EncodeToString(audio),
		"suffix":           suffix,
	}
	if strings.TrimSpace(language) != "" {
		in["language"] = language
	}
	out, err := w.call(ctx, "voice.transcribe", in)
	if err != nil {
		return nil, err
	}
	if anyBoolDef(out["unavailable"], false) {
		return nil, nil
	}
	text := strings.TrimSpace(anyString(out["text"]))
	if text == "" {
		return &VoiceTranscribeResult{Text: "", Language: anyString(out["language"])}, nil
	}
	return &VoiceTranscribeResult{Text: text, Language: anyString(out["language"])}, nil
}

func (w *RMDYVoiceWorker) Install(ctx context.Context, homeDir, component string) (bool, error) {
	out, err := w.call(ctx, "voice.install", map[string]any{
		"home_dir":         homeDir,
		tools.GoBoundField: true,
		"component":        component,
	})
	if err != nil {
		return false, err
	}
	if ok, has := out["ok"]; has && !anyBoolDef(ok, true) {
		msg := anyString(out["error"])
		if msg == "" {
			msg = "voice install refused"
		}
		return false, errors.New(msg)
	}
	return anyBoolDef(out["started"], false), nil
}

func (w *RMDYVisionWorker) Activate(ctx context.Context, homeDir string, enabled bool) (map[string]any, error) {
	return w.call(ctx, "vision.activate", map[string]any{
		"home_dir":         homeDir,
		tools.GoBoundField: true,
		"enabled":          enabled,
	})
}

func (w *RMDYVisionWorker) Install(ctx context.Context, homeDir, modelID, runtimeID string, preferCUDA bool) (map[string]any, error) {
	in := map[string]any{
		"home_dir":         homeDir,
		tools.GoBoundField: true,
		"prefer_cuda":      preferCUDA,
	}
	if strings.TrimSpace(modelID) != "" {
		in["model_id"] = modelID
	}
	if strings.TrimSpace(runtimeID) != "" {
		in["runtime_id"] = runtimeID
	}
	return w.call(ctx, "vision.install", in)
}

func (w *RMDYVisionWorker) CancelInstall(ctx context.Context, homeDir string) (map[string]any, error) {
	return w.call(ctx, "vision.cancel_install", map[string]any{"home_dir": homeDir, tools.GoBoundField: true})
}

func (w *RMDYVisionWorker) ReinstallRuntime(ctx context.Context, homeDir string, preferCUDA bool) (map[string]any, error) {
	return w.call(ctx, "vision.reinstall_runtime", map[string]any{
		"home_dir":         homeDir,
		tools.GoBoundField: true,
		"prefer_cuda":      preferCUDA,
	})
}

func (w *RMDYVisionWorker) Uninstall(ctx context.Context, homeDir string, keepModels bool) (map[string]any, error) {
	return w.call(ctx, "vision.uninstall", map[string]any{
		"home_dir":         homeDir,
		tools.GoBoundField: true,
		"keep_models":      keepModels,
	})
}

func (w *RMDYVisionWorker) Start(ctx context.Context, homeDir string) (map[string]any, error) {
	return w.call(ctx, "vision.start", map[string]any{"home_dir": homeDir, tools.GoBoundField: true})
}

func (w *RMDYVisionWorker) Stop(ctx context.Context, homeDir string) (map[string]any, error) {
	return w.call(ctx, "vision.stop", map[string]any{"home_dir": homeDir, tools.GoBoundField: true})
}

func (w *RMDYVisionWorker) Progress(ctx context.Context, homeDir string) map[string]any {
	out, err := w.call(ctx, "vision.progress", map[string]any{"home_dir": homeDir, tools.GoBoundField: true})
	if err != nil || out == nil {
		return nil
	}
	return out
}

func suffixFromContentType(ct string) string {
	ct = strings.ToLower(strings.TrimSpace(strings.Split(ct, ";")[0]))
	switch {
	case strings.Contains(ct, "wav"):
		return ".wav"
	case strings.Contains(ct, "mpeg"), strings.Contains(ct, "mp3"):
		return ".mp3"
	case strings.Contains(ct, "ogg"):
		return ".ogg"
	case strings.Contains(ct, "mp4"), strings.Contains(ct, "m4a"):
		return ".m4a"
	default:
		return ".webm"
	}
}
