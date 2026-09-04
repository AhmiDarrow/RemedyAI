package httpapi

import (
	"context"
)

// FixtureTurnRunner is a deterministic TurnRunner for tests and --listen smoke.
// It emits Tokens (default "Hello "/"world") and yields @@aborted when cancelled.
type FixtureTurnRunner struct {
	Tokens []string
}

// NewFixtureTurnRunner returns a runner with the desktop stream-smoke tokens.
func NewFixtureTurnRunner() *FixtureTurnRunner {
	return &FixtureTurnRunner{Tokens: []string{"Hello ", "world"}}
}

func (f *FixtureTurnRunner) RunTurn(ctx context.Context, _ TurnRequest, emit func(string) error) error {
	toks := f.Tokens
	if len(toks) == 0 {
		toks = []string{"Hello ", "world"}
	}
	for _, tok := range toks {
		select {
		case <-ctx.Done():
			_ = emit("@@aborted\n")
			return nil
		default:
		}
		if err := emit(tok); err != nil {
			return err
		}
	}
	return nil
}
