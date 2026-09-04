package httpapi

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"testing"

	"github.com/AhmiDarrow/RemedyAI/native/go/protocol"
	"github.com/AhmiDarrow/RemedyAI/native/go/tools"
)

type stubFrameCaller struct {
	fn func(context.Context, protocol.Frame) (protocol.Frame, error)
}

func (s stubFrameCaller) Call(ctx context.Context, frame protocol.Frame) (protocol.Frame, error) {
	return s.fn(ctx, frame)
}

func TestAttachMLWorkersFailClosedOnNilCaller(t *testing.T) {
	voice, vision, err := AttachMLWorkers(nil)
	if err == nil || voice != nil || vision != nil {
		t.Fatalf("want fail-closed nil attach, got voice=%v vision=%v err=%v", voice, vision, err)
	}
	if _, err := NewRMDYVoiceWorker(nil); err == nil {
		t.Fatal("NewRMDYVoiceWorker(nil) should fail")
	}
	if _, err := NewRMDYVisionWorker(nil); err == nil {
		t.Fatal("NewRMDYVisionWorker(nil) should fail")
	}
}

func TestAttachMLWorkersNonNilWhenCallerAttached(t *testing.T) {
	caller := stubFrameCaller{fn: func(_ context.Context, req protocol.Frame) (protocol.Frame, error) {
		var wire tools.WireRequest
		if err := json.Unmarshal(req.Payload, &wire); err != nil {
			t.Fatalf("wire: %v", err)
		}
		var out map[string]any
		switch wire.ToolID {
		case "voice.speak":
			out = map[string]any{
				"unavailable": false,
				"wav_b64":     base64.StdEncoding.EncodeToString([]byte("RIFF....WAV")),
				"sample_rate": 24000,
			}
		case "voice.install":
			out = map[string]any{"ok": true, "started": true}
		case "vision.activate":
			out = map[string]any{"ok": true, "mode": "local_files"}
		case "vision.progress":
			out = map[string]any{"phase": "idle", "message": ""}
		default:
			out = map[string]any{"ok": true}
		}
		payload, _ := json.Marshal(tools.WireResult{OK: true, Output: mustRawJSON(out)})
		return protocol.Frame{Kind: protocol.KindToolResult, CorrelationID: req.CorrelationID, Payload: payload}, nil
	}}

	voice, vision, err := AttachMLWorkers(caller)
	if err != nil || voice == nil || vision == nil {
		t.Fatalf("AttachMLWorkers: voice=%v vision=%v err=%v", voice, vision, err)
	}

	spoken, err := voice.Speak(context.Background(), "/tmp/home", VoiceSpeakRequest{Text: "hi"}, "female")
	if err != nil || spoken == nil || string(spoken.WAV) != "RIFF....WAV" || spoken.SampleRate != 24000 {
		t.Fatalf("Speak=%#v err=%v", spoken, err)
	}
	started, err := voice.Install(context.Background(), "/tmp/home", "tts")
	if err != nil || !started {
		t.Fatalf("Install started=%v err=%v", started, err)
	}
	act, err := vision.Activate(context.Background(), "/tmp/home", true)
	if err != nil || act["ok"] != true {
		t.Fatalf("Activate=%v err=%v", act, err)
	}
	prog := vision.Progress(context.Background(), "/tmp/home")
	if prog == nil || prog["phase"] != "idle" {
		t.Fatalf("Progress=%v", prog)
	}

	// Serve Config must receive the non-nil workers (wiring contract).
	srv, err := New(Config{
		HomeDir:      t.TempDir(),
		Token:        "test-token",
		VoiceWorker:  voice,
		VisionWorker: vision,
	})
	if err != nil {
		t.Fatal(err)
	}
	defer srv.Close()
	if srv.voice == nil || srv.vision == nil {
		t.Fatal("httpapi.Config did not retain attached voice/vision workers")
	}
}

func TestRMDYVoiceInstallFailClosedOnWorkerError(t *testing.T) {
	caller := stubFrameCaller{fn: func(_ context.Context, req protocol.Frame) (protocol.Frame, error) {
		payload, _ := json.Marshal(tools.WireResult{OK: true, Output: mustRawJSON(map[string]any{
			"ok": false, "started": false, "error": "Unknown voice piece 'nope'.",
		})})
		return protocol.Frame{Kind: protocol.KindToolResult, CorrelationID: req.CorrelationID, Payload: payload}, nil
	}}
	voice, err := NewRMDYVoiceWorker(caller)
	if err != nil {
		t.Fatal(err)
	}
	started, err := voice.Install(context.Background(), "/tmp/home", "nope")
	if err == nil || started {
		t.Fatalf("want install error, started=%v err=%v", started, err)
	}
}

func mustRawJSON(v any) json.RawMessage {
	raw, err := json.Marshal(v)
	if err != nil {
		panic(err)
	}
	return raw
}
