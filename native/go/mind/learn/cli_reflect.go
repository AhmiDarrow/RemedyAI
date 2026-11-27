package learn

import (
	"os"
	"path/filepath"
	"time"
)

// CLIStep is one step of `remedy learn reflect --steps` (cmd_skills.py
// _cmd_learn): the tool name, its argument keys (str()'d), the result text
// (cut to 200 characters here), whether it succeeded, and its error text.
type CLIStep struct {
	Tool    string
	ArgKeys []string
	Result  string
	Success bool
	Error   string
}

// CLISkill is the skill a reflection wrote or merged into.
type CLISkill struct {
	Name, Version, Instructions string
	Tags                        []string
}

// ReflectCLI is LearningLoop(skills_dir=<home>/skills).learn_from_trace for
// an ExecutionTrace built the way the CLI builds it: the title verbatim, no
// description or session, overall success assumed. It returns nil when the
// trace was refused, with the lifecycle reason.
func ReflectCLI(home, title string, steps []CLIStep, now time.Time) (*CLISkill, string, error) {
	skillsDir := filepath.Join(home, "skills")
	if err := os.MkdirAll(skillsDir, 0o700); err != nil {
		return nil, "", err
	}
	tr := trace{taskID: newUUID(), title: title}
	for _, s := range steps {
		tr.steps = append(tr.steps, tstep{
			tool: s.Tool, success: s.Success, summary: rcut(s.Result, 200), err: s.Error,
			argKeys: append([]string(nil), s.ArgKeys...),
		})
	}
	sk, _, reason, err := learnTrace(skillsDir, now, tr)
	if err != nil || sk == nil {
		return nil, reason, err
	}
	return &CLISkill{Name: sk.name, Version: sk.version, Instructions: sk.instructions, Tags: append([]string(nil), sk.tags...)}, reason, nil
}
