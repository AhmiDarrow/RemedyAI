package httpapi

import (
	"bufio"
	"context"
	"encoding/binary"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math"
	"os"
	"path/filepath"
	"regexp"
	"runtime"
	"strings"
	"sync"
	"time"
	"unicode/utf8"

	"github.com/AhmiDarrow/RemedyAI/native/go/core"
	"github.com/AhmiDarrow/RemedyAI/native/go/secret"
)

// Remedy's voice without Python: speech models run in the remedy-voice helper
// (native/voice, built on sherpa-onnx), which this file starts on first use
// and keeps warm. Downloads, settings, voice choice and text clean-up stay
// here; the helper only runs models on files it is pointed at.

// errVoiceFormat marks a recording the listening model cannot read.
var errVoiceFormat = errors.New("this recording is not WAV audio")

// kokoroSpeakers is the speaker order of kokoro-multi-lang-v1_0 (name -> sid),
// as its model card lists it.
var kokoroSpeakers = []string{
	"af_alloy", "af_aoede", "af_bella", "af_heart", "af_jessica", "af_kore", "af_nicole", "af_nova",
	"af_river", "af_sarah", "af_sky", "am_adam", "am_echo", "am_eric", "am_fenrir", "am_liam",
	"am_michael", "am_onyx", "am_puck", "am_santa", "bf_alice", "bf_emma", "bf_isabella", "bf_lily",
	"bm_daniel", "bm_fable", "bm_george", "bm_lewis", "ef_dora", "em_alex", "ff_siwis", "hf_alpha",
	"hf_beta", "hm_omega", "hm_psi", "if_sara", "im_nicola", "jf_alpha", "jf_gongitsune", "jf_nezumi",
	"jf_tebukuro", "jm_kumo", "pf_dora", "pm_alex", "pm_santa", "zf_xiaobei", "zf_xiaoni", "zf_xiaoxiao",
	"zf_xiaoyi", "zm_yunjian", "zm_yunxi", "zm_yunxia", "zm_yunyang", "em_santa",
}

func kokoroSpeakerID(name string) (int, bool) {
	name = strings.ToLower(strings.TrimSpace(name))
	for i, n := range kokoroSpeakers {
		if n == name {
			return i, true
		}
	}
	return 0, false
}

// kokoroLocaleVoices: the voice for a UI language the pack has a voice for.
// Languages it has none for (de, ko, ru, pl) stay on the English voice.
var kokoroLocaleVoices = map[string]map[string]string{
	"es":      {"female": "ef_dora", "male": "em_alex", "neutral": "ef_dora"},
	"fr":      {"female": "ff_siwis", "male": "ff_siwis", "neutral": "ff_siwis"},
	"it":      {"female": "if_sara", "male": "im_nicola", "neutral": "if_sara"},
	"pt":      {"female": "pf_dora", "male": "pm_alex", "neutral": "pf_dora"},
	"ja":      {"female": "jf_alpha", "male": "jm_kumo", "neutral": "jf_alpha"},
	"zh-Hans": {"female": "zf_xiaobei", "male": "zm_yunxi", "neutral": "zf_xiaobei"},
}

// whisperForUI: the listening language for a UI language ("" = detect).
var whisperForUI = map[string]string{
	"es": "es", "fr": "fr", "de": "de", "pt": "pt", "it": "it", "ru": "ru",
	"pl": "pl", "ja": "ja", "ko": "ko", "zh-Hans": "zh",
}

// uiLanguageForVoice is the concrete UI language, or "" for auto or unset.
func uiLanguageForVoice(homeDir string) string {
	code := strings.TrimSpace(cfgString(LoadConfig(homeDir), "ui_language", ""))
	if code == "" || strings.EqualFold(code, "auto") {
		return ""
	}
	return code
}

// ttsVoiceName picks the voice: the request's, then the owner's override,
// then the UI language's voice, then the one for her gender. An unknown name
// falls back to the gender voice, so a stale setting never silences her.
func ttsVoiceName(homeDir, gender, explicit string) string {
	fallback := voiceForGender(gender, "")
	pick := func(name string) (string, bool) {
		name = strings.ToLower(strings.TrimSpace(name))
		if _, ok := kokoroSpeakerID(name); ok {
			return name, true
		}
		return "", false
	}
	if v, ok := pick(explicit); ok {
		return v
	}
	if v, ok := pick(anyString(loadVoiceSettings(homeDir)["voice_override"])); ok {
		return v
	}
	if table, ok := kokoroLocaleVoices[uiLanguageForVoice(homeDir)]; ok {
		g := strings.ToLower(strings.TrimSpace(gender))
		if v, ok := table[g]; ok {
			return v
		}
		return table["female"]
	}
	return fallback
}

var (
	reFence    = regexp.MustCompile("(?s)```.*?```")
	reTick     = regexp.MustCompile("`([^`]*)`")
	reImage    = regexp.MustCompile(`!\[[^\]]*\]\([^)]*\)`)
	reLink     = regexp.MustCompile(`\[([^\]]+)\]\([^)]*\)`)
	reHeading  = regexp.MustCompile(`(?m)^#{1,6}\s+`)
	reBullet   = regexp.MustCompile(`(?m)^\s*[-*+]\s+`)
	reTableRow = regexp.MustCompile(`(?m)^\s*\|.*\|\s*$`)
	reEmphasis = regexp.MustCompile(`[*_~]{1,3}([^*_~]+)[*_~]{1,3}`)
	reURL      = regexp.MustCompile(`https?://\S+`)
	reSpace    = regexp.MustCompile(`\s+`)
)

// speakableText turns a chat reply into text worth hearing: code blocks are
// not read out, links and emphasis lose their marks, and a long reply stops at
// a sentence end.
func speakableText(markdown string, maxChars int) string {
	t := markdown
	if hard := maxChars * 8; len(t) > hard {
		t = t[:hard]
		for !utf8.ValidString(t) && len(t) > 0 {
			t = t[:len(t)-1]
		}
	}
	t = reFence.ReplaceAllString(t, " (code shown on screen) ")
	t = reTick.ReplaceAllString(t, "$1")
	t = reImage.ReplaceAllString(t, " ")
	t = reLink.ReplaceAllString(t, "$1")
	t = reHeading.ReplaceAllString(t, "")
	t = reBullet.ReplaceAllString(t, "")
	t = reTableRow.ReplaceAllString(t, " ")
	t = reEmphasis.ReplaceAllString(t, "$1")
	t = reURL.ReplaceAllString(t, " ")
	t = strings.TrimSpace(reSpace.ReplaceAllString(t, " "))
	r := []rune(t)
	if len(r) <= maxChars {
		return t
	}
	cut := string(r[:maxChars])
	dot := max(strings.LastIndex(cut, ". "), strings.LastIndex(cut, "! "), strings.LastIndex(cut, "? "))
	if dot > 200 {
		return cut[:dot+1]
	}
	return cut
}

// monoWAV16 rewrites PCM WAV audio (8/16/24/32-bit integer or 32-bit float,
// any channel count) as mono 16-bit PCM, which is what the helper reads.
func monoWAV16(data []byte) ([]byte, error) {
	if len(data) < 12 || string(data[0:4]) != "RIFF" || string(data[8:12]) != "WAVE" {
		return nil, errVoiceFormat
	}
	var (
		format, channels, bits uint16
		rate                   uint32
		pcm                    []byte
	)
	for off := 12; off+8 <= len(data); {
		id := string(data[off : off+4])
		size := int(binary.LittleEndian.Uint32(data[off+4 : off+8]))
		body := off + 8
		end := body + size
		if size < 0 || end > len(data) {
			end = len(data) // a recorder that never fixed up the length
		}
		switch id {
		case "fmt ":
			if end-body < 16 {
				return nil, errVoiceFormat
			}
			format = binary.LittleEndian.Uint16(data[body:])
			channels = binary.LittleEndian.Uint16(data[body+2:])
			rate = binary.LittleEndian.Uint32(data[body+4:])
			bits = binary.LittleEndian.Uint16(data[body+14:])
			if format == 0xFFFE && end-body >= 26 { // extensible: the real format follows
				format = binary.LittleEndian.Uint16(data[body+24:])
			}
		case "data":
			pcm = data[body:end]
		}
		off = end + size%2
		if size == 0 && id != "data" && id != "fmt " {
			off = body
		}
	}
	if channels == 0 || rate == 0 || pcm == nil {
		return nil, errVoiceFormat
	}
	width := int(bits / 8)
	sample := func(b []byte) (float64, bool) {
		switch {
		case format == 1 && bits == 8:
			return (float64(b[0]) - 128) / 128, true
		case format == 1 && bits == 16:
			return float64(int16(binary.LittleEndian.Uint16(b))) / 32768, true
		case format == 1 && bits == 24:
			v := int32(b[0]) | int32(b[1])<<8 | int32(int8(b[2]))<<16
			return float64(v) / 8388608, true
		case format == 1 && bits == 32:
			return float64(int32(binary.LittleEndian.Uint32(b))) / 2147483648, true
		case format == 3 && bits == 32:
			return float64(math.Float32frombits(binary.LittleEndian.Uint32(b))), true
		}
		return 0, false
	}
	if width == 0 {
		return nil, errVoiceFormat
	}
	frame := width * int(channels)
	frames := len(pcm) / frame
	out := make([]byte, 44+frames*2)
	copy(out[0:], "RIFF")
	binary.LittleEndian.PutUint32(out[4:], uint32(36+frames*2))
	copy(out[8:], "WAVEfmt ")
	binary.LittleEndian.PutUint32(out[16:], 16)
	binary.LittleEndian.PutUint16(out[20:], 1)
	binary.LittleEndian.PutUint16(out[22:], 1)
	binary.LittleEndian.PutUint32(out[24:], rate)
	binary.LittleEndian.PutUint32(out[28:], rate*2)
	binary.LittleEndian.PutUint16(out[32:], 2)
	binary.LittleEndian.PutUint16(out[34:], 16)
	copy(out[36:], "data")
	binary.LittleEndian.PutUint32(out[40:], uint32(frames*2))
	for i := 0; i < frames; i++ {
		var sum float64
		for c := 0; c < int(channels); c++ {
			v, ok := sample(pcm[i*frame+c*width:])
			if !ok {
				return nil, errVoiceFormat
			}
			sum += v
		}
		v := sum / float64(channels)
		if v > 1 {
			v = 1
		} else if v < -1 {
			v = -1
		}
		binary.LittleEndian.PutUint16(out[44+i*2:], uint16(int16(v*32767)))
	}
	return out, nil
}

// voiceHelperIdle is how long the helper stays loaded after its last request.
const voiceHelperIdle = 10 * time.Minute

// voiceHelper is the running remedy-voice process.
type voiceHelper struct {
	mu    sync.Mutex
	kill  func() // nil while no helper is running
	in    io.WriteCloser
	out   *bufio.Reader
	next  int
	timer *time.Timer
}

// voiceHelperPath finds remedy-voice: REMEDY_VOICE_BIN, else beside this
// program (where the installer puts it).
func voiceHelperPath() (string, error) {
	name := "remedy-voice"
	if runtime.GOOS == "windows" {
		name += ".exe"
	}
	if p := strings.TrimSpace(os.Getenv("REMEDY_VOICE_BIN")); p != "" {
		if fileExists(p) {
			return p, nil
		}
		return "", fmt.Errorf("REMEDY_VOICE_BIN does not exist: %s", p)
	}
	exe, err := os.Executable()
	if err != nil {
		return "", err
	}
	p := filepath.Join(filepath.Dir(exe), name)
	if !fileExists(p) {
		return "", fmt.Errorf("%s is not installed beside remedy-runtime", name)
	}
	return p, nil
}

func (h *voiceHelper) stopLocked() {
	if h.timer != nil {
		h.timer.Stop()
		h.timer = nil
	}
	if h.kill == nil {
		return
	}
	_ = h.in.Close()
	h.kill()
	h.kill, h.in, h.out = nil, nil, nil
}

func (h *voiceHelper) startLocked() error {
	if h.kill != nil {
		return nil
	}
	path, err := voiceHelperPath()
	if err != nil {
		return err
	}
	in, out, kill, err := voiceHelperSpawn(path)
	if err != nil {
		return err
	}
	h.kill, h.in, h.out = kill, in, bufio.NewReaderSize(out, 64*1024)
	return nil
}

// voiceHelperSpawn starts the helper with piped stdin/stdout. Like every
// other child of the runtime it goes through the native core: an authorized,
// hidden spawn in the runtime's job, so the helper cannot outlive it. Tests
// may replace it.
var voiceHelperSpawn = func(path string) (io.WriteCloser, io.Reader, func(), error) {
	key, err := secret.EnsureHostSigningKey("")
	if err != nil {
		return nil, nil, nil, err
	}
	if err := core.EnsureSigningKey(key); err != nil {
		return nil, nil, nil, err
	}
	argv := []string{filepath.Clean(path)}
	token, nowMS, err := core.IssueProcessSpawnToken(argv, nil, false, false)
	if err != nil {
		return nil, nil, nil, err
	}
	var proc core.PipedProcess
	if err := core.WithWriteJail(nil, func() error {
		var err error
		proc, err = core.ProcessSpawnPipedAuthorized(argv, "", nil, false, token, "", "", false, nowMS)
		return err
	}); err != nil {
		return nil, nil, nil, err
	}
	stdin, stdout, stderr := proc.Files()
	go func() { _, _ = io.Copy(io.Discard, stderr); _ = stderr.Close() }()
	var once sync.Once
	kill := func() {
		once.Do(func() {
			_ = core.ProcessKillTree(proc.PID)
			_ = stdout.Close()
			_ = core.ProcessClose(proc.Handle)
		})
	}
	return stdin, stdout, kill, nil
}

// call sends one request and waits for its reply. One request runs at a time:
// two models at once would fight over the same cores. Cancelling ctx stops the
// helper, which is the only way to interrupt a model mid-run.
func (h *voiceHelper) call(ctx context.Context, req map[string]any) (map[string]any, error) {
	h.mu.Lock()
	defer h.mu.Unlock()
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	if err := h.startLocked(); err != nil {
		return nil, err
	}
	if h.timer != nil {
		h.timer.Stop()
	}
	h.next++
	req["id"] = h.next
	line, err := json.Marshal(req)
	if err != nil {
		return nil, err
	}
	type reply struct {
		v   map[string]any
		err error
	}
	done := make(chan reply, 1)
	in, out := h.in, h.out
	go func() {
		if _, err := in.Write(append(line, '\n')); err != nil {
			done <- reply{nil, err}
			return
		}
		raw, err := out.ReadBytes('\n')
		if err != nil {
			done <- reply{nil, fmt.Errorf("the voice helper stopped: %w", err)}
			return
		}
		var v map[string]any
		if err := json.Unmarshal(raw, &v); err != nil {
			done <- reply{nil, fmt.Errorf("the voice helper sent a bad reply: %w", err)}
			return
		}
		done <- reply{v, nil}
	}()
	var r reply
	select {
	case r = <-done:
	case <-ctx.Done():
		h.stopLocked()
		return nil, ctx.Err()
	}
	if r.err != nil {
		h.stopLocked()
		return nil, r.err
	}
	h.timer = time.AfterFunc(voiceHelperIdle, func() {
		h.mu.Lock()
		defer h.mu.Unlock()
		h.stopLocked()
	})
	if !anyBoolDef(r.v["ok"], false) {
		msg := anyString(r.v["error"])
		if msg == "" {
			msg = "the voice helper failed"
		}
		return nil, errors.New(msg)
	}
	return r.v, nil
}

// NativeVoiceWorker implements VoiceWorker with the remedy-voice helper.
type NativeVoiceWorker struct {
	helper  voiceHelper
	install voiceInstalls
}

// NewNativeVoiceWorker never starts anything: the helper starts on first use.
func NewNativeVoiceWorker() *NativeVoiceWorker {
	return &NativeVoiceWorker{}
}

// Close stops the helper (runtime shutdown).
func (w *NativeVoiceWorker) Close() {
	w.helper.mu.Lock()
	defer w.helper.mu.Unlock()
	w.helper.stopLocked()
}

func (w *NativeVoiceWorker) Speak(ctx context.Context, homeDir string, req VoiceSpeakRequest, gender string) (*VoiceSpeakResult, error) {
	text := speakableText(req.Text, 1400)
	if text == "" {
		return nil, nil
	}
	settings := loadVoiceSettings(homeDir)
	speed := 1.0
	if req.Speed != nil && *req.Speed > 0 {
		speed = *req.Speed
	} else if v, ok := settings["speed"].(float64); ok && v > 0 {
		speed = v
	}
	out, err := os.CreateTemp("", "remedy-say-*.wav")
	if err != nil {
		return nil, err
	}
	outPath := out.Name()
	_ = out.Close()
	defer os.Remove(outPath)

	call := map[string]any{"op": "speak", "text": text, "out": outPath, "speed": speed}
	if anyString(settings["tts_quality"]) == "hq" && hqInstalled(homeDir) {
		// Her own voice: Pocket speaks in the voice of the reference clip.
		call["engine"] = "pocket"
		call["dir"] = voiceModelDir(homeDir, voicePackHQ)
		call["reference"] = voiceReferencePath(homeDir, gender)
	} else {
		if !ttsInstalled(homeDir) {
			return nil, nil
		}
		sid, _ := kokoroSpeakerID(ttsVoiceName(homeDir, gender, req.Voice))
		call["engine"] = "kokoro"
		call["dir"] = voiceModelDir(homeDir, voicePackTTS)
		call["sid"] = sid
	}
	reply, err := w.helper.call(ctx, call)
	if err != nil {
		return nil, err
	}
	wav, err := os.ReadFile(outPath)
	if err != nil {
		return nil, err
	}
	return &VoiceSpeakResult{WAV: wav, SampleRate: anyInt(reply["sample_rate"], 24000)}, nil
}

func (w *NativeVoiceWorker) Transcribe(ctx context.Context, homeDir string, audio []byte, _ string, language string) (*VoiceTranscribeResult, error) {
	if !sttInstalled(homeDir) {
		return nil, nil
	}
	wav, err := monoWAV16(audio)
	if err != nil {
		return nil, err
	}
	settings := loadVoiceSettings(homeDir)
	lang := strings.TrimSpace(language)
	if lang == "" {
		lang = strings.TrimSpace(anyString(settings["language"]))
	}
	if lang == "" {
		lang = whisperForUI[uiLanguageForVoice(homeDir)]
	}
	in, err := os.CreateTemp("", "remedy-hear-*.wav")
	if err != nil {
		return nil, err
	}
	inPath := in.Name()
	_, werr := in.Write(wav)
	_ = in.Close()
	defer os.Remove(inPath)
	if werr != nil {
		return nil, werr
	}
	reply, err := w.helper.call(ctx, map[string]any{
		"op": "transcribe", "dir": sttModelDir(homeDir, anyString(settings["stt_model"])),
		"wav": inPath, "language": lang,
	})
	if err != nil {
		return nil, err
	}
	return &VoiceTranscribeResult{
		Text:     strings.TrimSpace(anyString(reply["text"])),
		Language: anyString(reply["language"]),
	}, nil
}

// Install starts the download of a voice piece in the background and reports
// whether one was started (false: already there, or already arriving).
func (w *NativeVoiceWorker) Install(_ context.Context, homeDir, component string) (bool, error) {
	var packs []string
	switch strings.ToLower(strings.TrimSpace(component)) {
	case "tts":
		packs = []string{voicePackTTS}
	case "stt":
		packs = []string{voicePackSTT}
	case "chatterbox", "hq":
		packs = []string{voicePackHQ}
	case "all", "*", "pack", "voice":
		packs = []string{voicePackTTS, voicePackSTT}
		if anyString(loadVoiceSettings(homeDir)["tts_quality"]) == "hq" {
			packs = append(packs, voicePackHQ)
		}
	case "smart-turn", "smart_turn":
		return false, errors.New("Turn-taking is not used by this version, so there is nothing to download.")
	default:
		return false, fmt.Errorf("Unknown voice piece '%s'.", component)
	}
	started := false
	for _, p := range packs {
		if w.install.start(homeDir, p) {
			started = true
		}
	}
	return started, nil
}

// InstallState is what the status endpoint reports for a pack: nil when
// nothing was asked for since the runtime started.
func (w *NativeVoiceWorker) InstallState(pack string) any {
	return w.install.state(pack)
}
