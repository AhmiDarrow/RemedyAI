package httpapi

import (
	"archive/tar"
	"compress/bzip2"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"sync"
)

// The voice models and where they come from. Each is one archive from the
// sherpa-onnx project's model releases, checked against its pinned size (and
// sha256 where the release publishes one) before it is unpacked.

const (
	voicePackTTS = "tts"
	voicePackSTT = "stt"
	voicePackHQ  = "hq"
)

const sherpaModels = "https://github.com/k2-fsa/sherpa-onnx/releases/download/"

type voiceArchive struct {
	// Name is the archive's top folder and its file name without .tar.bz2.
	Name   string
	URL    string
	Size   int64
	SHA256 string // "" when the release publishes no digest
}

var kokoroArchive = voiceArchive{
	Name:   "kokoro-multi-lang-v1_0",
	URL:    sherpaModels + "tts-models/kokoro-multi-lang-v1_0.tar.bz2",
	Size:   349886476,
	SHA256: "60703707bc05ccd48d60e1eb723f38b07d6e474795a0b6a588d7220400e0f7a4",
}

var pocketArchive = voiceArchive{
	Name:   "sherpa-onnx-pocket-tts-int8-2026-01-26",
	URL:    sherpaModels + "tts-models/sherpa-onnx-pocket-tts-int8-2026-01-26.tar.bz2",
	Size:   98337865,
	SHA256: "f4da7c7bd465f7b91e89191487fe90504396a51d6f0bcf94d0787633e2657293",
}

// whisperArchives: the listening models, by the stt_model setting. The
// release publishes no digests for these, so the size is the check.
var whisperArchives = map[string]voiceArchive{
	"tiny":           {Name: "sherpa-onnx-whisper-tiny", Size: 116204861},
	"base":           {Name: "sherpa-onnx-whisper-base", Size: 207557382},
	"small":          {Name: "sherpa-onnx-whisper-small", Size: 639387718},
	"medium":         {Name: "sherpa-onnx-whisper-medium", Size: 1931372882},
	"large-v3":       {Name: "sherpa-onnx-whisper-large-v3", Size: 1068482488},
	"large-v3-turbo": {Name: "sherpa-onnx-whisper-turbo", Size: 563790207},
}

func whisperArchive(model string) voiceArchive {
	a, ok := whisperArchives[model]
	if !ok {
		a = whisperArchives["small"]
	}
	a.URL = sherpaModels + "asr-models/" + a.Name + ".tar.bz2"
	return a
}

// The two public-domain LibriVox clips her own voice is cloned from.
var voiceReferences = map[string]struct{ URL, SHA256 string }{
	"female": {
		URL:    "https://raw.githubusercontent.com/AhmiDarrow/RemedyAI/master/assets/voice-references/female.wav",
		SHA256: "fee8d3d4c2c55a64839ba862fcbf675707a1b2000aaa7dbff243dc96211ee5a6",
	},
	"male": {
		URL:    "https://raw.githubusercontent.com/AhmiDarrow/RemedyAI/master/assets/voice-references/male.wav",
		SHA256: "b6faf84951c98f1c52b8e8a113140e43c4ca877d7efba43fab24fb264132966a",
	},
}

// voiceReferencePath is the clip for her gender (neutral uses the female one).
func voiceReferencePath(homeDir, gender string) string {
	g := strings.ToLower(strings.TrimSpace(gender))
	if g != "male" {
		g = "female"
	}
	return filepath.Join(voiceHome(homeDir), "references", g+".wav")
}

// voicePackParent is the folder a pack's archive is unpacked in.
func voicePackParent(homeDir, pack string) string {
	switch pack {
	case voicePackSTT:
		return filepath.Join(voiceHome(homeDir), "stt")
	case voicePackHQ:
		return filepath.Join(voiceHome(homeDir), "hq")
	default:
		return filepath.Join(voiceHome(homeDir), "tts")
	}
}

// voiceModelDir is the unpacked model folder of the speaking packs.
func voiceModelDir(homeDir, pack string) string {
	if pack == voicePackHQ {
		return filepath.Join(voicePackParent(homeDir, pack), pocketArchive.Name)
	}
	return filepath.Join(voicePackParent(homeDir, pack), kokoroArchive.Name)
}

func sttModelDir(homeDir, model string) string {
	return filepath.Join(voicePackParent(homeDir, voicePackSTT), whisperArchive(model).Name)
}

func ttsInstalled(homeDir string) bool {
	dir := voiceModelDir(homeDir, voicePackTTS)
	return fileExists(filepath.Join(dir, "model.onnx")) &&
		fileExists(filepath.Join(dir, "voices.bin")) &&
		fileExists(filepath.Join(dir, "tokens.txt"))
}

// sttInstalled: the listening model the owner chose is unpacked.
func sttInstalled(homeDir string) bool {
	dir := sttModelDir(homeDir, anyString(loadVoiceSettings(homeDir)["stt_model"]))
	ents, err := os.ReadDir(dir)
	if err != nil {
		return false
	}
	var enc, dec, tok bool
	for _, e := range ents {
		n := e.Name()
		enc = enc || strings.HasSuffix(n, "-encoder.int8.onnx") || strings.HasSuffix(n, "-encoder.onnx")
		dec = dec || strings.HasSuffix(n, "-decoder.int8.onnx") || strings.HasSuffix(n, "-decoder.onnx")
		tok = tok || strings.HasSuffix(n, "-tokens.txt")
	}
	return enc && dec && tok
}

// hqInstalled: the cloning model and both reference clips are there.
func hqInstalled(homeDir string) bool {
	dir := voiceModelDir(homeDir, voicePackHQ)
	return fileExists(filepath.Join(dir, "lm_main.int8.onnx")) &&
		fileExists(filepath.Join(dir, "text_conditioner.onnx")) &&
		fileExists(voiceReferencePath(homeDir, "female")) &&
		fileExists(voiceReferencePath(homeDir, "male"))
}

// voiceInstalls tracks the downloads started since the runtime came up.
type voiceInstalls struct {
	mu     sync.Mutex
	states map[string]map[string]any
}

func (v *voiceInstalls) set(pack string, st map[string]any) {
	v.mu.Lock()
	defer v.mu.Unlock()
	if v.states == nil {
		v.states = map[string]map[string]any{}
	}
	v.states[pack] = st
}

func (v *voiceInstalls) state(pack string) any {
	v.mu.Lock()
	defer v.mu.Unlock()
	st, ok := v.states[pack]
	if !ok {
		return nil
	}
	out := make(map[string]any, len(st))
	for k, val := range st {
		out[k] = val
	}
	return out
}

func (v *voiceInstalls) busy(pack string) bool {
	v.mu.Lock()
	defer v.mu.Unlock()
	s, _ := v.states[pack]["status"].(string)
	return s == "downloading" || s == "loading"
}

// start begins fetching a pack unless it is already there or arriving.
func (v *voiceInstalls) start(homeDir, pack string) bool {
	if v.busy(pack) {
		return false
	}
	var (
		archive voiceArchive
		there   bool
	)
	switch pack {
	case voicePackSTT:
		model := anyString(loadVoiceSettings(homeDir)["stt_model"])
		archive, there = whisperArchive(model), sttInstalled(homeDir)
	case voicePackHQ:
		archive, there = pocketArchive, hqInstalled(homeDir)
	default:
		archive, there = kokoroArchive, ttsInstalled(homeDir)
	}
	if there {
		v.set(pack, map[string]any{"status": "done", "percent": 100, "model": archive.Name})
		return false
	}
	v.set(pack, map[string]any{"status": "downloading", "percent": 0, "model": archive.Name})
	go func() {
		err := fetchVoiceArchive(archive, voicePackParent(homeDir, pack), func(status string, pct int) {
			v.set(pack, map[string]any{"status": status, "percent": pct, "model": archive.Name})
		})
		if err == nil && pack == voicePackHQ {
			for g, ref := range voiceReferences {
				if err = fetchVoiceFile(ref.URL, voiceReferencePath(homeDir, g), ref.SHA256); err != nil {
					break
				}
			}
		}
		if err != nil {
			v.set(pack, map[string]any{"status": "error", "percent": 0, "model": archive.Name, "error": err.Error()})
			return
		}
		dropLegacyVoiceFiles(homeDir, pack)
		v.set(pack, map[string]any{"status": "done", "percent": 100, "model": archive.Name})
	}()
	return true
}

// RemoveLegacyPython deletes the private Python that older versions downloaded
// into the home folder to run their worker. Nothing starts it any more.
func RemoveLegacyPython(homeDir string) {
	_ = os.RemoveAll(filepath.Join(voiceHome(ResolveHomeDir(homeDir)), "runtime"))
}

// dropLegacyVoiceFiles removes what the Python voice kept for a pack, once the
// native pack that replaces it is in place: nothing reads those files any more
// and they are hundreds of megabytes (the cloning voice, gigabytes).
func dropLegacyVoiceFiles(homeDir, pack string) {
	root := voiceHome(homeDir)
	switch pack {
	case voicePackTTS:
		_ = os.Remove(filepath.Join(root, "tts", "kokoro-v1.0.onnx"))
		_ = os.Remove(filepath.Join(root, "tts", "voices-v1.0.bin"))
	case voicePackSTT:
		dir := filepath.Join(root, "stt")
		ents, _ := os.ReadDir(dir)
		for _, e := range ents {
			// The native packs are the sherpa-onnx-whisper-* folders; the rest is the
			// old download cache.
			if !strings.HasPrefix(e.Name(), "sherpa-onnx-") {
				_ = os.RemoveAll(filepath.Join(dir, e.Name()))
			}
		}
	case voicePackHQ:
		_ = os.RemoveAll(filepath.Join(root, "chatterbox"))
	}
}

// fetchVoiceFile downloads one small pinned file (a reference clip).
func fetchVoiceFile(url, dest, wantSHA string) error {
	if fileExists(dest) {
		if sum, err := sha256File(dest); err == nil && sum == wantSHA {
			return nil
		}
	}
	if err := os.MkdirAll(filepath.Dir(dest), 0o700); err != nil {
		return err
	}
	resp, err := http.Get(url)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("%s: the server answered %s", filepath.Base(dest), resp.Status)
	}
	part := dest + ".part"
	f, err := os.Create(part)
	if err != nil {
		return err
	}
	h := sha256.New()
	_, err = io.Copy(io.MultiWriter(f, h), io.LimitReader(resp.Body, 8<<20))
	if cerr := f.Close(); err == nil {
		err = cerr
	}
	if err == nil && hex.EncodeToString(h.Sum(nil)) != wantSHA {
		err = fmt.Errorf("%s did not match its pinned checksum", filepath.Base(dest))
	}
	if err != nil {
		_ = os.Remove(part)
		return err
	}
	return os.Rename(part, dest)
}

func sha256File(path string) (string, error) {
	f, err := os.Open(path)
	if err != nil {
		return "", err
	}
	defer f.Close()
	h := sha256.New()
	if _, err := io.Copy(h, f); err != nil {
		return "", err
	}
	return hex.EncodeToString(h.Sum(nil)), nil
}

// progressReader reports how much of the archive has arrived, 0 to 90: the
// last tenth is unpacking.
type progressReader struct {
	r      io.Reader
	total  int64
	seen   int64
	last   int
	report func(status string, pct int)
}

func (p *progressReader) Read(b []byte) (int, error) {
	n, err := p.r.Read(b)
	p.seen += int64(n)
	if pct := int(p.seen * 90 / p.total); pct != p.last && pct <= 90 {
		p.last = pct
		p.report("downloading", pct)
	}
	return n, err
}

// fetchVoiceArchive downloads a model archive into parent/<name>. The archive
// is saved first and checked against its pin, then unpacked beside its final
// place and moved in, so a half-finished download never looks installed.
func fetchVoiceArchive(a voiceArchive, parent string, report func(status string, pct int)) error {
	if err := os.MkdirAll(parent, 0o700); err != nil {
		return err
	}
	resp, err := http.Get(a.URL)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("%s: the server answered %s", a.Name, resp.Status)
	}
	archive := filepath.Join(parent, a.Name+".tar.bz2.part")
	defer os.Remove(archive)
	f, err := os.Create(archive)
	if err != nil {
		return err
	}
	h := sha256.New()
	// One byte past the pin is enough to know the file is not the pinned one.
	body := &progressReader{r: io.LimitReader(resp.Body, a.Size+1), total: a.Size, report: report}
	n, err := io.Copy(io.MultiWriter(f, h), body)
	if cerr := f.Close(); err == nil {
		err = cerr
	}
	if err != nil {
		return err
	}
	if n != a.Size {
		return fmt.Errorf("%s: the download is %d bytes, expected %d", a.Name, n, a.Size)
	}
	if a.SHA256 != "" && hex.EncodeToString(h.Sum(nil)) != a.SHA256 {
		return fmt.Errorf("%s did not match its pinned checksum", a.Name)
	}

	report("loading", 90)
	staging := filepath.Join(parent, a.Name+".unpacking")
	_ = os.RemoveAll(staging)
	defer os.RemoveAll(staging)
	if err := unpackVoiceArchive(archive, a.Name, staging); err != nil {
		return err
	}
	dropFullPrecisionTwins(staging)
	final := filepath.Join(parent, a.Name)
	_ = os.RemoveAll(final)
	return os.Rename(staging, final)
}

// unpackVoiceArchive unpacks the archive's top folder `name` into dest.
// Entries outside that folder, links and the sample recordings are skipped.
func unpackVoiceArchive(archive, name, dest string) error {
	f, err := os.Open(archive)
	if err != nil {
		return err
	}
	defer f.Close()
	tr := tar.NewReader(bzip2.NewReader(f))
	prefix := name + "/"
	for {
		hdr, err := tr.Next()
		if errors.Is(err, io.EOF) {
			return nil
		}
		if err != nil {
			return fmt.Errorf("%s: the archive is damaged: %w", name, err)
		}
		entry := strings.TrimPrefix(filepath.ToSlash(hdr.Name), "./")
		if !strings.HasPrefix(entry, prefix) {
			continue
		}
		rel := strings.TrimPrefix(entry, prefix)
		if rel == "" || strings.HasPrefix(rel, "test_wavs/") || !filepath.IsLocal(filepath.FromSlash(rel)) {
			continue
		}
		target := filepath.Join(dest, filepath.FromSlash(rel))
		switch hdr.Typeflag {
		case tar.TypeDir:
			if err := os.MkdirAll(target, 0o700); err != nil {
				return err
			}
		case tar.TypeReg:
			if err := os.MkdirAll(filepath.Dir(target), 0o700); err != nil {
				return err
			}
			out, err := os.OpenFile(target, os.O_CREATE|os.O_WRONLY|os.O_TRUNC, 0o600)
			if err != nil {
				return err
			}
			_, err = io.Copy(out, tr)
			if cerr := out.Close(); err == nil {
				err = cerr
			}
			if err != nil {
				return err
			}
		}
	}
}

// dropFullPrecisionTwins removes x.onnx (and its .weights) where x.int8.onnx
// is also there: the helper loads the quantized one, and the full one is most
// of the disk the listening models take.
func dropFullPrecisionTwins(dir string) {
	ents, err := os.ReadDir(dir)
	if err != nil {
		return
	}
	for _, e := range ents {
		n := e.Name()
		if !strings.HasSuffix(n, ".int8.onnx") {
			continue
		}
		full := strings.TrimSuffix(n, ".int8.onnx") + ".onnx"
		_ = os.Remove(filepath.Join(dir, full))
		_ = os.Remove(filepath.Join(dir, strings.TrimSuffix(full, ".onnx")+".weights"))
	}
}
