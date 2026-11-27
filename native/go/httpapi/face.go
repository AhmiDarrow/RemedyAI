package httpapi

import (
	"crypto/rand"
	"crypto/sha256"
	"encoding/binary"
	"encoding/hex"
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"strings"

	"github.com/AhmiDarrow/RemedyAI/native/go/mind/vigil"
)

// Remedy's face is drawn by the desktop from one 64-bit seed in
// REMEDY_HOME/face.seed. The runtime owns that seed and derives it from the
// setup: who the owner is, what they named Remedy, this install's own salt,
// and how many times they have asked for a new face. Two owners with the same
// names still get different faces, and the same setup always gives the same one.
//
//	face.seed       the seed in use (decimal)
//	face.seed.prev  the one before the last change, for face.restore
//	face.salt       random, made once per install
//	face.gen        how many faces this install has drawn

func (s *Server) facePath(name string) string {
	return filepath.Join(ResolveHomeDir(s.homeDir), name)
}

// faceSeedFor is the whole derivation. Fields are length-prefixed so
// ("ab","c") and ("a","bc") cannot collide.
func faceSeedFor(user, name, salt string, gen uint64) uint64 {
	h := sha256.New()
	for _, part := range []string{"remedy-face-v1", strings.TrimSpace(user), strings.TrimSpace(name), salt} {
		var n [8]byte
		binary.LittleEndian.PutUint64(n[:], uint64(len(part)))
		h.Write(n[:])
		h.Write([]byte(part))
	}
	var g [8]byte
	binary.LittleEndian.PutUint64(g[:], gen)
	h.Write(g[:])
	return binary.LittleEndian.Uint64(h.Sum(nil)[:8])
}

func (s *Server) faceSalt() (string, error) {
	path := s.facePath("face.salt")
	if b, err := os.ReadFile(path); err == nil && len(strings.TrimSpace(string(b))) >= 16 {
		return strings.TrimSpace(string(b)), nil
	}
	var b [16]byte
	if _, err := rand.Read(b[:]); err != nil {
		return "", err
	}
	salt := hex.EncodeToString(b[:])
	return salt, os.WriteFile(path, []byte(salt), 0o600)
}

func (s *Server) faceGen() uint64 {
	b, _ := os.ReadFile(s.facePath("face.gen"))
	n, _ := strconv.ParseUint(strings.TrimSpace(string(b)), 10, 64)
	return n
}

// drawFace derives the seed for generation gen from the current setup and
// makes it the face in use, keeping the one it replaces.
func (s *Server) drawFace(gen uint64) error {
	salt, err := s.faceSalt()
	if err != nil {
		return fmt.Errorf("could not make this install's face salt: %w", err)
	}
	cfg := LoadConfig(s.homeDir)
	seed := faceSeedFor(cfgString(cfg, "user_name", ""), cfgString(cfg, "name", ""), salt, gen)
	cur := s.facePath("face.seed")
	if old, err := os.ReadFile(cur); err == nil {
		if err := os.WriteFile(s.facePath("face.seed.prev"), old, 0o600); err != nil {
			return fmt.Errorf("could not keep the old face: %w", err)
		}
	}
	if err := os.WriteFile(s.facePath("face.gen"), []byte(strconv.FormatUint(gen, 10)), 0o600); err != nil {
		return fmt.Errorf("could not save the face count: %w", err)
	}
	if err := os.WriteFile(cur, []byte(strconv.FormatUint(seed, 10)), 0o600); err != nil {
		return fmt.Errorf("could not save the new face: %w", err)
	}
	return nil
}

// ensureFace gives Remedy her first face once setup has named the owner.
// Later renames leave the face alone; only face.regenerate draws a new one.
func (s *Server) ensureFace() {
	if s == nil {
		return
	}
	if _, err := os.Stat(s.facePath("face.seed")); err == nil {
		return
	}
	if strings.TrimSpace(cfgString(LoadConfig(s.homeDir), "user_name", "")) == "" {
		return
	}
	_ = s.drawFace(0)
}

func toolFaceRegenerate(s *Server, _ partnerToolInput) (any, error) {
	if err := s.drawFace(s.faceGen() + 1); err != nil {
		return nil, err
	}
	return map[string]any{"note": "New face, drawn from who you two are. It shows in the app; face.restore brings the old one back."}, nil
}

func toolFaceRestore(s *Server, _ partnerToolInput) (any, error) {
	cur, prev := s.facePath("face.seed"), s.facePath("face.seed.prev")
	old, err := os.ReadFile(prev)
	if err != nil {
		return nil, fmt.Errorf("there is no earlier face to bring back")
	}
	// Swap, so a second restore returns to the newer face.
	if now, err := os.ReadFile(cur); err == nil {
		_ = os.WriteFile(prev, now, 0o600)
	}
	if err := os.WriteFile(cur, old, 0o600); err != nil {
		return nil, fmt.Errorf("could not bring back the old face: %w", err)
	}
	return map[string]any{"note": "The earlier face is back."}, nil
}

// innerPublic is Remedy's own life as her face reads it: what she is doing by
// herself right now, and what she has done on her own so far. Every number is
// counted from what she recorded; nothing is estimated.
//
//	activity      "" | "vigil" (a night tick) | "learning" (skills, dream, life cycle)
//	nights        finished night acts, lifetime (vigil.json total_wakes)
//	dreams        those of them that were dreams (vigil.json total_dreams)
//	words         terms she has coined in her inner language
//	routine_runs  times her routines have run
//
// The journal is a ring, so it is only the fallback when vigil.json is absent.
func (s *Server) innerPublic() map[string]any {
	act, _ := s.innerAct.Load().(string)
	nights, dreams := 0, 0
	env := mindEnv(s.homeDir)
	if _, err := os.Stat(env.Path(vigil.StateFile)); err == nil {
		if st, err := vigil.Load(env); err == nil {
			nights, dreams = st.TotalWakes, st.TotalDreams
		}
	} else {
		entries, _ := vigil.ReadJournal(env)
		nights, dreams = vigil.CountJournal(entries)
	}
	words := 0
	if lex, ok := readInnerLanguagePublic(ResolveHomeDir(s.homeDir))["lexicon"].(map[string]any); ok {
		words, _ = lex["count"].(int)
	}
	runs := 0
	if h := s.partner; h != nil {
		h.mu.Lock()
		for _, r := range h.st.Routines {
			runs += r.Runs
		}
		h.mu.Unlock()
	}
	return map[string]any{"activity": act, "mood": s.currentMood(), "nights": nights, "dreams": dreams, "words": words, "routine_runs": runs}
}
