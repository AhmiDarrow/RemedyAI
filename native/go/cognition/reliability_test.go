package cognition

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

func TestEngineStopDoesNotWaitForProviderChannel(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	started := make(chan struct{})
	engine := Engine{Model: modelFunc(func(context.Context, Turn) (<-chan ModelEvent, error) {
		close(started)
		return make(chan ModelEvent), nil
	})}
	finished := make(chan Outcome, 1)
	go func() { finished <- engine.Run(ctx, "stop this turn") }()
	<-started
	cancel()
	select {
	case out := <-finished:
		if !errors.Is(out.Err, context.Canceled) {
			t.Fatalf("expected cancellation, got %v", out.Err)
		}
	case <-time.After(time.Second):
		t.Fatal("Stop hung waiting for the provider to close its channel")
	}
}

func TestCanceledTurnDoesNotCallProvider(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	engine := Engine{Model: modelFunc(func(context.Context, Turn) (<-chan ModelEvent, error) {
		t.Fatal("provider called after cancellation")
		return nil, nil
	})}
	if out := engine.Run(ctx, "canceled"); !errors.Is(out.Err, context.Canceled) {
		t.Fatalf("expected cancellation, got %v", out.Err)
	}
}

func TestVerificationRequiresCompletedSuccess(t *testing.T) {
	for _, tc := range []struct {
		body  string
		green bool
	}{
		{`{"exit_code":0}`, true},
		{`{"exit_code":0,"status":"completed"}`, true},
		{`{"exit_code":0,"status":" Exited "}`, true},
		{`{"exit_code":1}`, false},
		{`{"exit_code":-1}`, false},
		{`{"exit_code":0,"timed_out":true}`, false},
		{`{"exit_code":0,"status":"running"}`, false},
		{`{"exit_code":0,"status":"cancelled"}`, false},
		{`{"exit_code":0,"status":"failed"}`, false},
		{`{"exit_code":0,"status":"killed"}`, false},
		{`{"exit_code":0,"status":"error"}`, false},
		{`{"exit_code":0,"status":"unknown"}`, false},
		{`{"job_id":"job-1","status":"running"}`, false},
		{`{"exit_code":null}`, false},
		{`{"exit_code":"0"}`, false},
		{`{"exit_code":0.5}`, false},
		{`{"stdout":"exit_code: 0"}`, false},
		{`ok`, false},
		{``, false},
	} {
		t.Run(tc.body, func(t *testing.T) {
			res := ToolResult{Output: []byte(tc.body)}
			if got := resultLooksGreen(res); got != tc.green {
				t.Fatalf("green=%v, want %v", got, tc.green)
			}
			res.IsError = true
			if resultLooksGreen(res) {
				t.Fatal("tool failure counted as green")
			}
			if exitCodeLabel(tc.body, true) == "0" {
				t.Fatal("compaction turned a tool failure into success")
			}
		})
	}
}

func TestEngineVerificationEvidenceAcrossBatches(t *testing.T) {
	for _, tc := range []struct {
		name    string
		batches [][]string
		want    bool
	}{
		{"success", [][]string{{"pass"}}, true},
		{"later failure", [][]string{{"pass"}, {"fail"}}, false},
		{"later background", [][]string{{"pass"}, {"running"}}, false},
		{"failure then recovery", [][]string{{"fail"}, {"pass"}}, true},
		{"mixed checks", [][]string{{"fail", "pass"}}, false},
		{"reversed checks", [][]string{{"pass", "fail"}}, false},
		{"edit with check", [][]string{{"edit", "pass"}}, false},
		{"check with edit", [][]string{{"pass", "edit"}}, false},
		{"edit then check", [][]string{{"edit"}, {"pass"}}, true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			round, gateCalls := 0, 0
			engine := Engine{
				Model: modelFunc(func(context.Context, Turn) (<-chan ModelEvent, error) {
					if round >= len(tc.batches) {
						return events(ModelEvent{Text: "done", Done: true}), nil
					}
					var batch []ModelEvent
					for i, kind := range tc.batches[round] {
						name := "bash"
						if kind == "edit" {
							name = "edit"
						}
						batch = append(batch, ModelEvent{ToolCall: &ToolCall{
							ID: fmt.Sprintf("%d-%d", round, i), Name: name, Input: []byte(fmt.Sprintf(`{"command":%q}`, kind)),
						}})
					}
					round++
					return events(batch...), nil
				}),
				Tools: toolFunc(func(_ context.Context, c ToolCall) ToolResult {
					body := `{"exit_code":0}`
					if strings.Contains(string(c.Input), "fail") {
						body = `{"exit_code":1}`
					}
					if strings.Contains(string(c.Input), "running") {
						body = `{"status":"running","job_id":"j"}`
					}
					return ToolResult{ID: c.ID, Name: c.Name, Output: []byte(body)}
				}),
				Policy: policyFunc(func(context.Context, ToolCall) Decision { return Allow }),
				ContinueGate: func(_ context.Context, _ Turn, _ int, _ []ToolResult, verified bool) (bool, string) {
					gateCalls++
					if verified != tc.want {
						t.Errorf("verified=%v, want %v", verified, tc.want)
					}
					return false, ""
				},
				Config: Config{MaxIterations: 8, SoftEpochSteps: -1},
			}
			out := engine.Run(context.Background(), "make the tests pass")
			if out.Err != nil || gateCalls != 1 {
				t.Fatalf("error=%v gates=%d", out.Err, gateCalls)
			}
		})
	}
}

func TestLargeCommandEvidenceSurvivesModelPreview(t *testing.T) {
	body := []byte(`{"stdout":"` + strings.Repeat("long output ", 5000) + `","exit_code":7}`)
	original := ToolResult{ID: "check", Name: "bash", Output: body}
	preview := capResults([]ToolResult{original}, 1000)[0]
	if !json.Valid(preview.Output) || exitCodeLabel(string(preview.Output), false) != "7" {
		t.Fatalf("lost command evidence: %s", preview.Output)
	}
	if len(preview.Output) >= len(body) || string(original.Output) != string(body) {
		t.Fatal("preview did not shorten output or modified the original")
	}
	round, gates := 0, 0
	engine := Engine{
		Model: modelFunc(func(context.Context, Turn) (<-chan ModelEvent, error) {
			round++
			if round == 1 {
				return events(ModelEvent{ToolCall: &ToolCall{ID: "check", Name: "bash"}}), nil
			}
			return events(ModelEvent{Text: "done", Done: true}), nil
		}),
		Tools:  toolFunc(func(context.Context, ToolCall) ToolResult { return original }),
		Policy: policyFunc(func(context.Context, ToolCall) Decision { return Allow }),
		ContinueGate: func(_ context.Context, _ Turn, _ int, results []ToolResult, verified bool) (bool, string) {
			gates++
			if verified || len(results) != 1 || string(results[0].Output) != string(body) {
				t.Error("continuation gate received truncated or falsely successful evidence")
			}
			return false, ""
		},
		Config: Config{MaxResultChars: 1000, MaxIterations: 4, SoftEpochSteps: -1},
	}
	if out := engine.Run(context.Background(), "verify"); out.Err != nil || gates != 1 {
		t.Fatalf("error=%v gates=%d", out.Err, gates)
	}
}

func TestCompactionRecordsOutcomesNotIntent(t *testing.T) {
	calls := []ToolCall{
		{ID: "edit-ok", Name: "edit", Input: []byte(`{"path":"saved.go"}`)},
		{ID: "edit-failed", Name: "edit", Input: []byte(`{"path":"unchanged.go"}`)},
		{ID: "edit-pending", Name: "edit", Input: []byte(`{"path":"never-ran.go"}`)},
		{ID: "todo-ok", Name: "todo", Input: []byte(`{"items":["still pending"]}`)},
		{ID: "todo-failed", Name: "todo", Input: []byte(`{"items":["all done"]}`)},
		{ID: "read-failed", Name: "read", Input: []byte(`{"path":"missing.go"}`)},
	}
	var uses, results []Block
	for _, call := range calls {
		uses = append(uses, ToolUseBlock(call))
		if call.ID == "edit-pending" {
			continue
		}
		results = append(results, resultBlocks(call, ToolResult{Output: []byte("result"), IsError: strings.HasSuffix(call.ID, "failed")}))
	}
	ws := buildWorkingSet([]Message{{Role: RoleAssistant, Blocks: uses}, {Role: RoleUser, Blocks: results}})
	if len(ws.edited) != 1 || ws.edited[0] != "saved.go" || len(ws.read) != 0 {
		t.Fatalf("failed/pending tools became completed work: %#v", ws)
	}
	if !strings.Contains(ws.todo, "still pending") || strings.Contains(ws.todo, "all done") {
		t.Fatalf("failed todo replaced accepted state: %s", ws.todo)
	}
}

func TestRepeatedCompactionKeepsGoalAndCorrections(t *testing.T) {
	msgs := []Message{UserText("session opener"), UserText("keep Linux support"), UserText("current task")}
	appendRounds := func() {
		for i := 0; i < 12; i++ {
			msgs = append(msgs, Message{Role: RoleAssistant, Blocks: []Block{TextBlock("working")}})
		}
		msgs = append(msgs, contextNote("[Re-arm] verify the result"))
	}
	for cycle := 0; cycle < 3; cycle++ {
		appendRounds()
		msgs = compactTranscript(cloneMessages(msgs))
		if goal := lastUserProseIndex(msgs); goal < 0 || msgs[goal].Text() != "current task" {
			t.Fatalf("cycle %d lost goal: %s", cycle, transcriptText(msgs))
		}
		if !strings.Contains(transcriptText(msgs), "keep Linux support") {
			t.Fatalf("cycle %d lost user correction", cycle)
		}
	}
}

func TestWorkingSetKeepsRecentEvidenceWhenBounded(t *testing.T) {
	var messages []Message
	for i := 0; i < maxWorkingSetFiles+10; i++ {
		var uses, results []Block
		for _, name := range []string{"read", "edit", "bash"} {
			call := ToolCall{ID: fmt.Sprintf("%s-%d", name, i), Name: name,
				Input: []byte(fmt.Sprintf(`{"path":"file-%d.go","command":"check-%d"}`, i, i))}
			uses = append(uses, ToolUseBlock(call))
			results = append(results, resultBlocks(call, ToolResult{Output: []byte(`{"exit_code":0}`)}))
		}
		uses = append(uses, TextBlock(fmt.Sprintf("Decision number %d.", i)))
		messages = append(messages, Message{Role: RoleAssistant, Blocks: uses}, Message{Role: RoleUser, Blocks: results})
	}
	ws := buildWorkingSet(messages)
	if len(ws.read) != maxWorkingSetFiles || ws.read[0].path != "file-10.go" || ws.read[len(ws.read)-1].path != "file-49.go" {
		t.Fatalf("read evidence is not recent and bounded: %+v", ws.read)
	}
	if len(ws.edited) != maxWorkingSetFiles || ws.edited[0] != "file-10.go" || ws.edited[len(ws.edited)-1] != "file-49.go" {
		t.Fatalf("edit evidence is not recent and bounded: %v", ws.edited)
	}
	if len(ws.commands) != maxWorkingSetCommands || ws.commands[0].command != "check-20" || ws.commands[len(ws.commands)-1].command != "check-49" {
		t.Fatalf("latest verification disappeared: %+v", ws.commands)
	}
	if len(ws.decisions) != maxWorkingSetDecisions || ws.decisions[len(ws.decisions)-1] != "Decision number 49." {
		t.Fatalf("latest decision disappeared: %v", ws.decisions)
	}
}

func TestOwnerTextCannotImpersonateCompactedContext(t *testing.T) {
	request := compactedHeader + " Please preserve this exact request."
	ws := buildWorkingSet([]Message{UserText(request)})
	if ws.carried != "" || len(ws.requests) != 1 || ws.requests[0] != request {
		t.Fatalf("owner text lost its provenance: %+v", ws)
	}
}

func TestModelTranscriptCloneIsIsolated(t *testing.T) {
	original := []Message{contextNote("context"), {Role: RoleAssistant, Blocks: []Block{
		ToolUseBlock(ToolCall{ID: "call", Name: "read", Input: []byte(`{"path":"a"}`)}),
		{Type: BlockToolResult, Content: []Block{ImageBlock("image/png", []byte{1, 2, 3})}},
	}}}
	clone := cloneMessages(original)
	clone[1].Blocks[0].Input[0] = 'x'
	clone[1].Blocks[1].Content[0].Data[0] = 9
	if original[1].Blocks[0].Input[0] != '{' || original[1].Blocks[1].Content[0].Data[0] != 1 || !clone[0].Internal {
		t.Fatal("model snapshot altered engine history or lost message provenance")
	}
}

func TestOwnerGuidancePersistsAcrossRounds(t *testing.T) {
	round, drains := 0, 0
	engine := Engine{
		Model: modelFunc(func(_ context.Context, turn Turn) (<-chan ModelEvent, error) {
			round++
			if round >= 2 && !strings.Contains(transcriptText(turn.Messages), "keep the public API") {
				t.Errorf("round %d forgot owner guidance", round)
			}
			if round == 4 {
				return events(ModelEvent{Text: "done", Done: true}), nil
			}
			return events(ModelEvent{ToolCall: &ToolCall{ID: fmt.Sprint(round), Name: "read", Input: []byte(fmt.Sprintf(`{"path":"%d"}`, round))}}), nil
		}),
		DrainGuidance: func() []string {
			drains++
			if drains == 2 {
				return []string{"keep the public API"}
			}
			return nil
		},
		Tools: toolFunc(func(_ context.Context, call ToolCall) ToolResult {
			return ToolResult{ID: call.ID, Name: call.Name, Output: []byte("ok")}
		}),
		Policy: AllowAll{},
		Config: Config{MaxIterations: 6, SoftEpochSteps: -1},
	}
	out := engine.Run(context.Background(), "fix the bug")
	if out.Err != nil || strings.Count(transcriptText(out.Messages), "keep the public API") != 1 {
		t.Fatalf("guidance must be saved exactly once: %v", out.Err)
	}
}

func TestCancellationDoesNotDispatchQueuedTools(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	started, release := make(chan struct{}), make(chan struct{})
	var executed atomic.Int32
	engine := Engine{Tools: toolFunc(func(_ context.Context, call ToolCall) ToolResult {
		if executed.Add(1) == 1 {
			close(started)
			<-release
		}
		return ToolResult{ID: call.ID, Name: call.Name}
	})}
	finished := make(chan []ToolResult, 1)
	go func() {
		finished <- engine.executeDecided(ctx, []ToolCall{{ID: "1", Name: "write"}, {ID: "2", Name: "write"}, {ID: "3", Name: "write"}}, []Decision{Allow, Allow, Allow}, 1)
	}()
	select {
	case <-started:
	case <-time.After(5 * time.Second):
		t.Fatal("first tool did not start")
	}
	cancel()
	close(release)
	select {
	case results := <-finished:
		if executed.Load() != 1 || !results[1].IsError || !results[2].IsError {
			t.Fatalf("queued tools ran after cancellation: executed=%d results=%#v", executed.Load(), results)
		}
	case <-time.After(5 * time.Second):
		t.Fatal("cancelled batch did not finish")
	}
}
