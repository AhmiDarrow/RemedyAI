package dream

import (
	"regexp"
	"strings"
	"unicode/utf8"

	sf "github.com/AhmiDarrow/RemedyAI/native/go/mind/internal/statefile"
	"github.com/AhmiDarrow/RemedyAI/native/go/mind/soul"
)

// Partner dreams (partner_dream.py): bind their goals to how I will show up.
// The Partner Memory profile is not read here (it lives in the Python
// memory store); goals come from the soul field only.

var (
	stripPrefixRE = regexp.MustCompile(`(?i)^(goal:|toward |stay with:|ongoing focus:|from now on we |we will always )\s*`)
	goalWordRE    = regexp.MustCompile(`(?i)\b(goal|ship|finish|launch|family|life)\b`)
)

func cleanGoal(text string) string {
	t := stripPrefixRE.ReplaceAllString(strings.TrimSpace(text), "")
	t = strings.Trim(spaceRE.ReplaceAllString(t, " "), " .")
	return sf.Cut(t, 90)
}

func dedupe(lines []string) []string {
	seen := map[string]bool{}
	var out []string
	for _, line := range lines {
		k := sf.Cut(spaceRE.ReplaceAllString(strings.ToLower(strings.TrimSpace(line)), " "), 120)
		if utf8.RuneCountInString(k) < 8 || seen[k] {
			continue
		}
		seen[k] = true
		out = append(out, sf.Cut(strings.TrimSpace(line), 200))
	}
	return out
}

func firstN(s []string, n int) []string {
	if len(s) > n {
		return s[:n]
	}
	return s
}

func userGoals(f *soul.Field) []string {
	var goals []string
	for _, p := range f.Pledges {
		if !soul.LooksLikeWorkResidue(p) {
			goals = append(goals, p)
		}
	}
	threads := f.Relational.OpenThreads
	if len(threads) > 6 {
		threads = threads[len(threads)-6:]
	}
	for _, t := range threads {
		if soul.LooksLikeWorkResidue(t) {
			continue
		}
		if goalWordRE.MatchString(t) || utf8.RuneCountInString(t) >= 16 {
			goals = append(goals, t)
		}
	}
	var cleaned []string
	for _, g := range goals {
		if c := cleanGoal(g); utf8.RuneCountInString(c) >= 8 {
			cleaned = append(cleaned, c)
		}
	}
	return firstN(dedupe(cleaned), 8)
}

func selfMoves(f *soul.Field) []string {
	var moves []string
	rel := f.Relational
	switch strings.TrimSpace(rel.HelpMode) {
	case "silent-doer":
		moves = append(moves, "act first; one short summary after tools")
	case "pair":
		moves = append(moves, "think with them, then implement — do not vanish into a plan essay")
	case "coach":
		moves = append(moves, "name the next step, then do it with them")
	case "sparring":
		moves = append(moves, "challenge weakly-held ideas; keep building")
	}
	switch strings.TrimSpace(rel.CorrectionStyle) {
	case "blunt":
		moves = append(moves, "no defensive monologue; fix what they named")
	case "gentle":
		moves = append(moves, "correct softly; still change the work")
	}
	recent := lastN(f.Episodes, 5)
	for _, e := range recent {
		if e.UserStance == "frustrated" {
			moves = append(moves, "fix the blocker they named before adding scope")
			break
		}
	}
	for _, e := range recent {
		if e.UserStance == "focused" {
			moves = append(moves, "stay in the build — tools over narration until verified")
			break
		}
	}
	lessons := f.OrganismLessons
	if len(lessons) > 4 {
		lessons = lessons[len(lessons)-4:]
	}
	for _, l := range lessons {
		bit := strings.TrimSpace(l.Lesson)
		if bit == "" {
			bit = strings.TrimSpace(l.Summary)
		}
		if utf8.RuneCountInString(bit) >= 12 {
			moves = append(moves, sf.Cut(bit, 100))
		}
	}
	if len(moves) == 0 {
		moves = append(moves, "resume, do not restart; verify before claiming done")
	}
	return firstN(dedupe(moves), 8)
}

// ComposePartnerDreams binds their goals to my next partner moves.
func ComposePartnerDreams(f *soul.Field, limit int) []string {
	limit = max(1, limit)
	goals, moves := userGoals(f), selfMoves(f)
	if len(moves) == 0 {
		moves = []string{"resume, do not restart; verify before claiming done"}
	}
	var dreams []string
	if len(goals) > 0 {
		for i, g := range firstN(goals, limit) {
			dreams = append(dreams, "Toward "+g+": "+moves[i%len(moves)])
		}
	} else {
		dreams = append(dreams, "As their partner: "+moves[0])
	}
	return firstN(dedupe(dreams), limit)
}

// ApplyPartnerDreams writes dreams onto the field, composed ones first, and
// returns how many new lines landed.
func ApplyPartnerDreams(f *soul.Field, dreams []string) int {
	key := func(d string) string {
		return sf.Cut(spaceRE.ReplaceAllString(strings.ToLower(strings.TrimSpace(d)), " "), 120)
	}
	have := map[string]bool{}
	for _, d := range f.FutureDreams {
		have[sf.Cut(spaceRE.ReplaceAllString(strings.ToLower(d), " "), 120)] = true
	}
	added := 0
	for _, d := range dreams {
		k := key(d)
		if utf8.RuneCountInString(k) < 8 || have[k] {
			continue
		}
		f.FutureDreams = append(f.FutureDreams, sf.Cut(d, 200))
		have[k] = true
		added++
	}
	if len(dreams) > 0 {
		out := append([]string(nil), dreams...)
		for _, d := range f.FutureDreams {
			if !sf.Contains(dreams, d) {
				out = append(out, d)
			}
		}
		f.FutureDreams = firstN(out, 8)
	}
	return added
}
