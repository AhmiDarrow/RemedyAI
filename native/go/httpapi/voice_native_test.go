package httpapi

import (
	"context"
	"encoding/binary"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestSpeakableTextDropsWhatShouldNotBeReadAloud(t *testing.T) {
	in := "# Done\n\nI ran `go test` and it **passed**. See [the log](https://x.test/log).\n\n```go\nfunc main() {}\n```\n- one\n- two https://x.test/raw"
	got := speakableText(in, 1400)
	for _, bad := range []string{"#", "`", "**", "https://", "func main", "[the log]"} {
		if strings.Contains(got, bad) {
			t.Fatalf("%q still in %q", bad, got)
		}
	}
	for _, want := range []string{"Done", "go test", "passed", "the log", "(code shown on screen)", "one two"} {
		if !strings.Contains(got, want) {
			t.Fatalf("%q missing from %q", want, got)
		}
	}
	long := strings.Repeat("A full sentence here. ", 200)
	cut := speakableText(long, 1400)
	if len([]rune(cut)) > 1400 || !strings.HasSuffix(cut, ".") {
		t.Fatalf("a long reply must stop at a sentence end within the limit, got %d runes ending %q", len([]rune(cut)), cut[len(cut)-10:])
	}
	if speakableText("```\nonly code\n```", 1400) == "" {
		t.Fatal("a reply that is only code still says so")
	}
}

// wavFile builds a PCM16 WAV with the given channels and samples.
func wavFile(rate uint32, channels uint16, samples []int16) []byte {
	out := make([]byte, 44+len(samples)*2)
	copy(out, "RIFF")
	binary.LittleEndian.PutUint32(out[4:], uint32(36+len(samples)*2))
	copy(out[8:], "WAVEfmt ")
	binary.LittleEndian.PutUint32(out[16:], 16)
	binary.LittleEndian.PutUint16(out[20:], 1)
	binary.LittleEndian.PutUint16(out[22:], channels)
	binary.LittleEndian.PutUint32(out[24:], rate)
	binary.LittleEndian.PutUint32(out[28:], rate*uint32(channels)*2)
	binary.LittleEndian.PutUint16(out[32:], channels*2)
	binary.LittleEndian.PutUint16(out[34:], 16)
	copy(out[36:], "data")
	binary.LittleEndian.PutUint32(out[40:], uint32(len(samples)*2))
	for i, s := range samples {
		binary.LittleEndian.PutUint16(out[44+i*2:], uint16(s))
	}
	return out
}

func TestMonoWAV16DownmixesAndRefusesOtherFormats(t *testing.T) {
	stereo := wavFile(48000, 2, []int16{1000, 3000, -2000, -4000})
	mono, err := monoWAV16(stereo)
	if err != nil {
		t.Fatal(err)
	}
	if ch := binary.LittleEndian.Uint16(mono[22:]); ch != 1 {
		t.Fatalf("channels=%d", ch)
	}
	if rate := binary.LittleEndian.Uint32(mono[24:]); rate != 48000 {
		t.Fatalf("rate=%d", rate)
	}
	if n := binary.LittleEndian.Uint32(mono[40:]); n != 4 {
		t.Fatalf("data bytes=%d", n)
	}
	a := int16(binary.LittleEndian.Uint16(mono[44:]))
	b := int16(binary.LittleEndian.Uint16(mono[46:]))
	if a < 1990 || a > 2010 || b > -2990 || b < -3010 {
		t.Fatalf("downmix gave %d, %d", a, b)
	}
	for _, bad := range [][]byte{nil, []byte("OggS....not wav at all"), stereo[:20]} {
		if _, err := monoWAV16(bad); !errors.Is(err, errVoiceFormat) {
			t.Fatalf("want errVoiceFormat, got %v", err)
		}
	}
}

func TestVoiceNameOrderAndSpeakerIDs(t *testing.T) {
	home := t.TempDir()
	t.Setenv("REMEDY_HOME", home)
	InvalidateConfigCache()
	if got := ttsVoiceName(home, "male", ""); got != "am_michael" {
		t.Fatalf("gender voice: %s", got)
	}
	if got := ttsVoiceName(home, "female", "BF_Emma"); got != "bf_emma" {
		t.Fatalf("explicit voice: %s", got)
	}
	// A name the pack does not have never silences her.
	if got := ttsVoiceName(home, "female", "no_such_voice"); got != "af_heart" {
		t.Fatalf("unknown voice: %s", got)
	}
	if _, err := saveVoiceSettings(home, map[string]any{"voice_override": "af_sky"}); err != nil {
		t.Fatal(err)
	}
	if got := ttsVoiceName(home, "male", ""); got != "af_sky" {
		t.Fatalf("override: %s", got)
	}
	for name, want := range map[string]int{"af_alloy": 0, "af_heart": 3, "af_sky": 10, "am_michael": 16, "zm_yunyang": 52, "em_santa": 53} {
		if got, ok := kokoroSpeakerID(name); !ok || got != want {
			t.Fatalf("%s: sid %d ok=%v, want %d", name, got, ok, want)
		}
	}
	for _, table := range kokoroLocaleVoices {
		for _, v := range table {
			if _, ok := kokoroSpeakerID(v); !ok {
				t.Fatalf("locale voice %s is not in the pack", v)
			}
		}
	}
	for g, v := range genderVoices {
		if _, ok := kokoroSpeakerID(v); !ok {
			t.Fatalf("%s voice %s is not in the pack", g, v)
		}
	}
}

func TestListeningModelIsInstalledPerChosenModel(t *testing.T) {
	home := t.TempDir()
	if sttInstalled(home) {
		t.Fatal("nothing is installed in an empty home")
	}
	dir := sttModelDir(home, "small")
	_ = os.MkdirAll(dir, 0o700)
	for _, n := range []string{"small-encoder.onnx", "small-encoder.int8.onnx", "small-decoder.onnx", "small-decoder.int8.onnx", "small-tokens.txt"} {
		_ = os.WriteFile(filepath.Join(dir, n), []byte("x"), 0o600)
	}
	if !sttInstalled(home) {
		t.Fatal("the small model is there")
	}
	dropFullPrecisionTwins(dir)
	if fileExists(filepath.Join(dir, "small-encoder.onnx")) || !fileExists(filepath.Join(dir, "small-encoder.int8.onnx")) {
		t.Fatal("the full-precision twin goes, the quantized file stays")
	}
	if !sttInstalled(home) {
		t.Fatal("still installed with only the quantized files")
	}
	// Choosing another model means that one has to arrive.
	if _, err := saveVoiceSettings(home, map[string]any{"stt_model": "base"}); err != nil {
		t.Fatal(err)
	}
	if sttInstalled(home) {
		t.Fatal("base is not installed")
	}
}

func TestLegacyVoiceFilesGoOnlyForTheirPack(t *testing.T) {
	home := t.TempDir()
	old := []string{"tts/kokoro-v1.0.onnx", "tts/voices-v1.0.bin", "stt/models--Systran--faster-whisper-small/blob", "chatterbox/hf/weights"}
	keep := []string{"tts/kokoro-multi-lang-v1_0/model.onnx", "stt/sherpa-onnx-whisper-small/small-tokens.txt", "voice.json", "references/female.wav"}
	for _, rel := range append(append([]string{}, old...), keep...) {
		p := filepath.Join(home, "voice", filepath.FromSlash(rel))
		_ = os.MkdirAll(filepath.Dir(p), 0o700)
		_ = os.WriteFile(p, []byte("x"), 0o600)
	}
	dropLegacyVoiceFiles(home, voicePackSTT)
	if fileExists(filepath.Join(home, "voice", "stt", "models--Systran--faster-whisper-small", "blob")) {
		t.Fatal("the old listening cache goes with the listening pack")
	}
	if !fileExists(filepath.Join(home, "voice", "tts", "kokoro-v1.0.onnx")) {
		t.Fatal("the old speaking model stays until its own pack arrives")
	}
	dropLegacyVoiceFiles(home, voicePackTTS)
	dropLegacyVoiceFiles(home, voicePackHQ)
	for _, rel := range old {
		if fileExists(filepath.Join(home, "voice", filepath.FromSlash(rel))) {
			t.Fatalf("%s should be gone", rel)
		}
	}
	for _, rel := range keep {
		if !fileExists(filepath.Join(home, "voice", filepath.FromSlash(rel))) {
			t.Fatalf("%s must stay", rel)
		}
	}
}

func TestNativeVoiceWithoutModelsOrHelper(t *testing.T) {
	home := t.TempDir()
	w := NewNativeVoiceWorker()
	defer w.Close()
	// No model: unavailable, and the helper is never started.
	if out, err := w.Speak(context.Background(), home, VoiceSpeakRequest{Text: "hello"}, "female"); out != nil || err != nil {
		t.Fatalf("speak without a model: %v %v", out, err)
	}
	if out, err := w.Transcribe(context.Background(), home, wavFile(16000, 1, []int16{1, 2}), "audio/wav", ""); out != nil || err != nil {
		t.Fatalf("transcribe without a model: %v %v", out, err)
	}
	if w.helper.kill != nil {
		t.Fatal("the helper must not start without a model")
	}
	if _, err := w.Install(context.Background(), home, "nope"); err == nil {
		t.Fatal("an unknown piece is refused")
	}
	if _, err := w.Install(context.Background(), home, "smart-turn"); err == nil {
		t.Fatal("turn-taking has nothing to download")
	}
	if w.InstallState(voicePackTTS) != nil {
		t.Fatal("nothing was asked for yet")
	}
}
