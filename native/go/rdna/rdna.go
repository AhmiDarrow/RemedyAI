// Package rdna defines Remedy's experimental semantic intent representation.
package rdna

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/json"
	"errors"
	"fmt"
	"io"
)

const Version = 1

var (
	ErrInvalidIntent        = errors.New("invalid RDNA intent")
	ErrUnsupportedVersion   = errors.New("unsupported RDNA version")
	ErrExperimentalDisabled = errors.New("RDNVM is experimental and disabled")
)

type Action string

const (
	AcquireInformation Action = "acquire_information"
	ChangeState        Action = "change_state"
	Communicate        Action = "communicate"
)

type Constraints struct {
	Capabilities    []string `json:"capabilities"`
	ReadOnly        bool     `json:"read_only"`
	NoCredentials   bool     `json:"no_credentials"`
	OwnerCheckpoint bool     `json:"owner_checkpoint"`
}
type Intent struct {
	Version     int         `json:"version"`
	ID          string      `json:"id"`
	Action      Action      `json:"action"`
	Target      string      `json:"target"`
	Constraints Constraints `json:"constraints"`
	Expected    []string    `json:"expected"`
	Fallbacks   []Intent    `json:"fallbacks,omitempty"`
}
type ToolBinding struct {
	ToolID  string `json:"tool_id"`
	Version uint32 `json:"version"`
}
type Step struct {
	ID          string      `json:"id"`
	Binding     ToolBinding `json:"binding"`
	Target      string      `json:"target"`
	Constraints Constraints `json:"constraints"`
	Expected    []string    `json:"expected"`
	Fallbacks   []Step      `json:"fallbacks,omitempty"`
}
type Plan struct {
	Version    int      `json:"version"`
	IntentHash [32]byte `json:"intent_hash"`
	Root       Step     `json:"root"`
}

func Validate(intent Intent) error {
	if intent.Version != Version {
		return ErrUnsupportedVersion
	}
	if intent.ID == "" || intent.Target == "" || len(intent.Expected) == 0 {
		return ErrInvalidIntent
	}
	switch intent.Action {
	case AcquireInformation:
	case ChangeState:
		if intent.Constraints.ReadOnly {
			return fmt.Errorf("%w: read-only state change", ErrInvalidIntent)
		}
	case Communicate:
		if !intent.Constraints.OwnerCheckpoint {
			return fmt.Errorf("%w: communication requires owner checkpoint", ErrInvalidIntent)
		}
	default:
		return ErrInvalidIntent
	}
	for _, fallback := range intent.Fallbacks {
		if err := Validate(fallback); err != nil {
			return err
		}
	}
	return nil
}
func Canonical(intent Intent) ([]byte, error) {
	if err := Validate(intent); err != nil {
		return nil, err
	}
	normalize(&intent)
	return json.Marshal(intent)
}
func Compile(intent Intent, bindings map[Action]ToolBinding) (Plan, error) {
	canonical, err := Canonical(intent)
	if err != nil {
		return Plan{}, err
	}
	root, err := compileStep(intent, bindings)
	if err != nil {
		return Plan{}, err
	}
	return Plan{Version: Version, IntentHash: sha256.Sum256(canonical), Root: root}, nil
}
func compileStep(intent Intent, bindings map[Action]ToolBinding) (Step, error) {
	binding, ok := bindings[intent.Action]
	if !ok || binding.ToolID == "" || binding.Version == 0 {
		return Step{}, fmt.Errorf("%w: no tool binding for %s", ErrInvalidIntent, intent.Action)
	}
	step := Step{ID: intent.ID, Binding: binding, Target: intent.Target, Constraints: intent.Constraints, Expected: append([]string(nil), intent.Expected...)}
	for _, fallback := range intent.Fallbacks {
		compiled, err := compileStep(fallback, bindings)
		if err != nil {
			return Step{}, err
		}
		step.Fallbacks = append(step.Fallbacks, compiled)
	}
	return step, nil
}
func normalize(intent *Intent) {
	intent.Constraints.Capabilities = append([]string(nil), intent.Constraints.Capabilities...)
	for i := range intent.Fallbacks {
		normalize(&intent.Fallbacks[i])
	}
}

type StepExecutor interface {
	ExecuteStep(context.Context, Step) error
}
type VM struct {
	Enabled  bool
	Executor StepExecutor
}

func (vm VM) Execute(ctx context.Context, plan Plan) error {
	if !vm.Enabled {
		return ErrExperimentalDisabled
	}
	if plan.Version != Version {
		return ErrUnsupportedVersion
	}
	if vm.Executor == nil {
		return errors.New("missing RDNVM executor")
	}
	return vm.Executor.ExecuteStep(ctx, plan.Root)
}
func Decode(raw []byte) (Intent, error) {
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.DisallowUnknownFields()
	var intent Intent
	if err := decoder.Decode(&intent); err != nil {
		return Intent{}, err
	}
	var trailing any
	if err := decoder.Decode(&trailing); !errors.Is(err, io.EOF) {
		if err == nil {
			return Intent{}, errors.New("trailing RDNA intent data")
		}
		return Intent{}, err
	}
	if err := Validate(intent); err != nil {
		return Intent{}, err
	}
	return intent, nil
}
