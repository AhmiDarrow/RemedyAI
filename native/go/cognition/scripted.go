package cognition

import "context"

// ScriptedModel replays a fixed sequence of Stream rounds.
// Round i is consumed on the i-th Stream call (1-based iteration).
// If fewer rounds than iterations remain, the last round is reused.
type ScriptedModel struct {
	Rounds   [][]ModelEvent
	LastTurn Turn // most recent Stream argument (tests)
}

func (m *ScriptedModel) Stream(_ context.Context, turn Turn) (<-chan ModelEvent, error) {
	m.LastTurn = turn
	if len(m.Rounds) == 0 {
		ch := make(chan ModelEvent)
		close(ch)
		return ch, nil
	}
	idx := turn.Iteration - 1
	if idx < 0 {
		idx = 0
	}
	if idx >= len(m.Rounds) {
		idx = len(m.Rounds) - 1
	}
	round := m.Rounds[idx]
	ch := make(chan ModelEvent, len(round))
	for _, ev := range round {
		ch <- ev
	}
	close(ch)
	return ch, nil
}

// DenyAll is a Policy that denies every tool call.
type DenyAll struct{}

func (DenyAll) Decide(context.Context, ToolCall) Decision { return Deny }

// AllowAll is a Policy that allows every tool call.
type AllowAll struct{}

func (AllowAll) Decide(context.Context, ToolCall) Decision { return Allow }

// EchoTools returns each call's input as output. Test double only — production
// turns use the Tool ABI registry (see httpapi.RegistryToolExecutor).
type EchoTools struct{}

func (EchoTools) Execute(_ context.Context, call ToolCall) ToolResult {
	return ToolResult{ID: call.ID, Name: call.Name, Output: call.Input}
}
