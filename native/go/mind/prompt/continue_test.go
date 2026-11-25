package prompt

import (
	"encoding/json"
	"os"
	"strings"
	"testing"
)

func intp(n int) *int { return &n }

// The expected values in the three tables below were recorded from the
// Python (react_policy.message_wants_tools, build_engine.looks_like_build_request,
// text_claims_completion, text_admits_unknown, agency_tool_promise_claim,
// looks_like_pseudo_tools, summarize_tool_evidence), not derived from the Go.

var wantsCases = []struct {
	goal         string
	wants, build bool
}{
	{"", false, false},
	{"hi", false, false},
	{"Hi!", false, false},
	{"hey there", false, false},
	{"Hi keep going", true, false},
	{"Hey, continue", true, false},
	{"Hi reme", false, false},
	{"thanks", false, false},
	{"ok", false, false},
	{"what is the capital of France?", false, false},
	{"who are you", false, false},
	{"what tools should I use to implement X", false, true},
	{"how are you feeling today?", false, false},
	{"1 + 1", false, false},
	{"2*2=", false, false},
	{"reply only STILLALIVE", false, false},
	{"say hello", false, false},
	{"fix the failing test in parser.py", true, true},
	{"create a file hello.py that prints hi", true, true},
	{"fix the bug in the build script", true, true},
	{"implement a retry loop", true, true},
	{"review the repo", true, true},
	{"can you add a dark mode", true, false},
	{"why is this failing?", true, false},
	{"it's ok we'll get there", false, false},
	{"continue", true, false},
	{"proceed with all fixes", true, false},
	{"go ahead", true, false},
	{"yes please proceed", true, false},
	{"look at my screen and critique this", true, false},
	{"what's on my clipboard", false, false},
	{"make me a calculator app", true, true},
	{"open C:\\Users\\me\\notes.txt", true, false},
	{"read src/main.rs", true, false},
	{"tell me a joke", false, false},
	{"[\"fix the bug\", \"in parser.py\"]", true, true},
	{"<tool_calls><invoke name=\"bash\">", false, false},
	{"bash_exec(\"ls\")", false, false},
	{"set up the project from scratch", true, true},
	{"why is the sky blue", false, false},
	{"explain how the build works", true, true},
	{"the quick brown fox jumps over the lazy dog and keeps going", false, false},
	{"we need to ship this today", true, true},
	{"audit the codebase for secrets", true, true},
	{"refactor the settings panel", true, true},
	{"the about window is broken", true, false},
	{"thank you so much for that", false, false},
	{"good morning", false, false},
	{"how do I fix the about window", true, false},
	{"write a script that renames files", true, true},
	{"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA\n```\nx\nx\nx\nx\nx\n", false, true},
	{"interesting idea about cooking dinner tonight please", true, false},
	{"play it", true, true},
	{"ok then", false, false},
	{"list my skills", false, false},
	{"what skills do you have", false, false},
	{"install ripgrep", true, false},
	{"Is the server up?", false, false},
	{"what's wrong", true, false},
	{"status?", true, false},
}

func TestContinueWantsToolsAndBuildGoal(t *testing.T) {
	for _, c := range wantsCases {
		if got := messageWantsTools(c.goal); got != c.wants {
			t.Errorf("messageWantsTools(%q) = %v, Python %v", c.goal, got, c.wants)
		}
		if got := looksLikeBuildRequest(c.goal); got != c.build {
			t.Errorf("looksLikeBuildRequest(%q) = %v, Python %v", c.goal, got, c.build)
		}
	}
}

var textCases = []struct {
	text                             string
	claims, unknown, promise, pseudo bool
}{
	{"", false, false, false, false},
	{"Done.", true, false, false, false},
	{"All done!", true, false, false, false},
	{"Paris.", false, false, false, false},
	{"Sure, I'll create hello.py for you now.", false, false, false, false},
	{"Let me review the code now.", false, false, true, false},
	{"I'll review it", false, false, true, false},
	{"I will use tools now", false, false, true, false},
	{"Activating skill now", false, false, true, false},
	{"Let me look at the file.", false, false, true, false},
	{"I'll check the tests and fix them.", false, false, true, false},
	{"Here is the answer.", false, false, false, false},
	{"xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx let me fix", false, false, false, false},
	{"xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx let me fix", false, false, false, false},
	{"xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx activating skill", false, false, true, false},
	{"The tests pass.", true, false, false, false},
	{"tests passed", true, false, false, false},
	{"Everything is now working.", true, false, false, false},
	{"It is now ready", true, false, false, false},
	{"I don't know how to do this", false, true, false, false},
	{"I could not find any docs.", false, true, false, false},
	{"i'm not sure how", false, true, false, false},
	{"```json\n{\n \"", false, false, false, true},
	{"```json", false, false, false, true},
	{"tool_c", false, false, false, true},
	{"<tool_calls>", false, false, false, true},
	{"Tools are disabled in plan mode.", false, false, false, false},
	{"bash_exec(\"ls\")", false, false, false, true},
	{"read(\"x\") && glob(\"y\")", false, false, false, true},
	{"{\"name\": \"file_write\", \"arguments\": {}}", false, false, false, true},
	{"<|tool_call>call:Bash{command:ls}", false, false, false, true},
	{"get_settings", false, false, false, false},
	{"just prose, nothing special here", false, false, false, false},
	{"<", false, false, false, true},
	{"｜DSML｜tool_calls", false, false, false, true},
	{"The invoke keyword is fine", false, false, false, false},
	{"x && y", false, false, false, false},
	{"Done! All tests pass now.", true, false, false, false},
	{"I'm going to implement the thing", false, false, true, false},
	{"let me test", false, false, true, false},
	{"debugging now", false, false, true, false},
}

func TestContinueTextHeuristics(t *testing.T) {
	for _, c := range textCases {
		if got := textClaimsCompletion(c.text); got != c.claims {
			t.Errorf("textClaimsCompletion(%q) = %v, Python %v", c.text, got, c.claims)
		}
		if got := textAdmitsUnknown(c.text); got != c.unknown {
			t.Errorf("textAdmitsUnknown(%q) = %v, Python %v", c.text, got, c.unknown)
		}
		if got := agencyToolPromiseClaim(c.text); got != c.promise {
			t.Errorf("agencyToolPromiseClaim(%q) = %v, Python %v", c.text, got, c.promise)
		}
		if got := looksLikePseudoTools(c.text); got != c.pseudo {
			t.Errorf("looksLikePseudoTools(%q) = %v, Python %v", c.text, got, c.pseudo)
		}
	}
}

type evidenceWant struct {
	mutates, explores               int
	verifySeen, verifyFailed        bool
	failedName                      string
	failedExit                      *int
	lastVerifyOK, mutateAfterVerify bool
	names                           []string
}

var evidenceCases = []struct {
	rows []map[string]any
	want evidenceWant
}{
	{[]map[string]any{}, evidenceWant{mutates: 0, explores: 0, verifySeen: false, verifyFailed: false, failedName: "", failedExit: nil, lastVerifyOK: false, mutateAfterVerify: false, names: []string{}}},
	{[]map[string]any{{"name": "edit", "ok": true, "tail": "edited"}}, evidenceWant{mutates: 1, explores: 0, verifySeen: false, verifyFailed: false, failedName: "", failedExit: nil, lastVerifyOK: false, mutateAfterVerify: true, names: []string{"edit"}}},
	{[]map[string]any{{"name": "bash", "ok": false, "tail": "exit_code=1 FAILED", "exit_code": 1}}, evidenceWant{mutates: 0, explores: 0, verifySeen: true, verifyFailed: true, failedName: "bash", failedExit: intp(1), lastVerifyOK: false, mutateAfterVerify: false, names: []string{"bash"}}},
	{[]map[string]any{{"name": "bash", "ok": true, "tail": "exit_code=0 4 passed", "exit_code": 0}}, evidenceWant{mutates: 0, explores: 0, verifySeen: true, verifyFailed: false, failedName: "", failedExit: nil, lastVerifyOK: true, mutateAfterVerify: false, names: []string{"bash"}}},
	{[]map[string]any{{"name": "bash", "ok": true, "tail": "exit code: 2 boom"}}, evidenceWant{mutates: 0, explores: 0, verifySeen: true, verifyFailed: true, failedName: "bash", failedExit: intp(2), lastVerifyOK: false, mutateAfterVerify: false, names: []string{"bash"}}},
	{[]map[string]any{{"name": "bash", "ok": true, "tail": "ModuleNotFoundError: No module named x", "exit_code": "3"}}, evidenceWant{mutates: 0, explores: 0, verifySeen: true, verifyFailed: true, failedName: "bash", failedExit: intp(3), lastVerifyOK: false, mutateAfterVerify: false, names: []string{"bash"}}},
	{[]map[string]any{{"name": "bash", "ok": true, "tail": "exit_code=5", "exit_code": "abc"}}, evidenceWant{mutates: 0, explores: 0, verifySeen: true, verifyFailed: true, failedName: "bash", failedExit: intp(5), lastVerifyOK: false, mutateAfterVerify: false, names: []string{"bash"}}},
	{[]map[string]any{{"name": "bash", "ok": nil, "tail": "fine"}, {"name": "write_file", "ok": true, "tail": ""}}, evidenceWant{mutates: 1, explores: 0, verifySeen: true, verifyFailed: false, failedName: "", failedExit: nil, lastVerifyOK: true, mutateAfterVerify: true, names: []string{"bash", "write_file"}}},
	{[]map[string]any{{"name": "web.search", "ok": true, "tail": ""}, {"name": "Delegate", "ok": true, "tail": ""}}, evidenceWant{mutates: 1, explores: 1, verifySeen: false, verifyFailed: false, failedName: "", failedExit: nil, lastVerifyOK: false, mutateAfterVerify: true, names: []string{"web.search", "Delegate"}}},
	{[]map[string]any{{"name": "", "ok": true}, {"name": "my-test-runner", "ok": false, "tail": "command not found"}}, evidenceWant{mutates: 0, explores: 0, verifySeen: true, verifyFailed: true, failedName: "my-test-runner", failedExit: nil, lastVerifyOK: false, mutateAfterVerify: false, names: []string{"my-test-runner"}}},
	{[]map[string]any{{"name": "grep", "ok": true}, {"name": "unknown_tool", "ok": true}}, evidenceWant{mutates: 0, explores: 2, verifySeen: false, verifyFailed: false, failedName: "", failedExit: nil, lastVerifyOK: false, mutateAfterVerify: false, names: []string{"grep", "unknown_tool"}}},
}

func TestContinueToolEvidence(t *testing.T) {
	for i, c := range evidenceCases {
		in := parseContinueInput(map[string]any{"last_results": c.rows})
		ev := summarizeToolEvidence(in.LastResults)
		w := c.want
		same := ev.mutates == w.mutates && ev.explores == w.explores &&
			ev.verifySeen == w.verifySeen && ev.verifyFailed == w.verifyFailed &&
			ev.failedName == w.failedName && ev.lastVerifyOK == w.lastVerifyOK &&
			ev.mutateAfterVerify == w.mutateAfterVerify &&
			strings.Join(ev.names, "|") == strings.Join(w.names, "|") &&
			(ev.failedExit == nil) == (w.failedExit == nil) &&
			(ev.failedExit == nil || *ev.failedExit == *w.failedExit)
		if !same {
			t.Errorf("case %d: got %+v, Python %+v", i, ev, w)
		}
	}
}

// Steps of testdata/should_continue.json whose recorded answer came from a
// source this port leaves out, and the answer this port gives instead.
//
//	step 2: Python said "unverified" because the build ledger written by
//	step 1 (write_steps > 0, no green verify) was still open
//	(prompt_assemble.py 757-761). Without the ledger the input-only checks
//	run on: nothing in the batch, engine flag false but tool_count is 0, no
//	narrated promise ("I'll create" is not in the promise list), no open
//	mission, and a work goal that ran no tools -> the wants_tools nudge.
var continueFallThrough = map[int]struct {
	cont   bool
	reason string
	nudge  string
}{
	2: {true, "wants_tools", nudgeUnfinished},
}

func TestContinueFixtureReplay(t *testing.T) {
	raw, err := os.ReadFile("testdata/should_continue.json")
	if err != nil {
		t.Fatal(err)
	}
	var fx struct {
		Steps []struct {
			Input  map[string]any `json:"input"`
			Output map[string]any `json:"output"`
		} `json:"steps"`
	}
	if err := json.Unmarshal(raw, &fx); err != nil {
		t.Fatal(err)
	}
	states := map[string]*ContinueState{}
	exact, fell := 0, 0
	for i, s := range fx.Steps {
		in := parseContinueInput(s.Input)
		st := states[in.Goal]
		if st == nil {
			st = &ContinueState{}
			states[in.Goal] = st
		}
		cont, nudge, reason := decideContinue(in, st)
		wantCont, _ := s.Output["continue"].(bool)
		wantNudge, _ := s.Output["nudge"].(string)
		wantReason, _ := s.Output["reason"].(string)
		if ft, ok := continueFallThrough[i]; ok {
			fell++
			wantCont, wantNudge, wantReason = ft.cont, ft.nudge, ft.reason
		} else {
			exact++
		}
		if cont != wantCont || nudge != wantNudge || reason != wantReason {
			t.Errorf("step %d: got (%v, %q, %q), want (%v, %q, %q)", i, cont, nudge, reason, wantCont, wantNudge, wantReason)
		}
	}
	if exact != len(fx.Steps)-len(continueFallThrough) || fell != len(continueFallThrough) {
		t.Errorf("exact=%d fallthrough=%d of %d steps", exact, fell, len(fx.Steps))
	}
}

func rowsOf(rows ...map[string]any) []any {
	out := make([]any, len(rows))
	for i, r := range rows {
		out[i] = r
	}
	return out
}

type call struct {
	in     map[string]any
	reason string
	cont   bool
}

func TestContinueBranchesAndGate(t *testing.T) {
	editOK := map[string]any{"name": "edit", "ok": true, "tail": "edited"}
	bashRed := map[string]any{"name": "bash", "ok": false, "tail": "exit_code=1 FAILED", "exit_code": 1}
	bashMissing := map[string]any{"name": "bash", "ok": false, "tail": "ModuleNotFoundError: No module named foo"}
	bashGreen := map[string]any{"name": "bash", "ok": true, "tail": "exit_code=0", "exit_code": 0}
	base := func(goal, text string, tools int, rows []any, extra map[string]any) map[string]any {
		m := map[string]any{"goal": goal, "text": text, "tool_count": tools, "last_results": rows}
		for k, v := range extra {
			m[k] = v
		}
		return m
	}
	cases := []struct {
		name  string
		calls []call
	}{
		{"chat mode", []call{{base("fix it", "done", 0, nil, map[string]any{"chat_mode": true}), "chat_mode", false}}},
		{"plain question", []call{{base("what is the capital of France?", "Paris.", 0, nil, nil), "done", false}}},
		{"verify failed claims done, repeats", []call{
			{base("fix the failing test in parser.py", "Done! All tests pass now.", 2, rowsOf(editOK, bashRed), nil), "verify_failed", true},
			{base("fix the failing test in parser.py", "Done! All tests pass now.", 2, rowsOf(editOK, bashRed), nil), "verify_failed", true},
		}},
		{"verify failed but text does not claim done", []call{
			{base("fix the failing test in parser.py", "The test is still red.", 2, rowsOf(bashRed), nil), "done", false},
		}},
		{"knowledge gap once, then plain verify_failed", []call{
			{base("fix the failing test in parser.py", "Fixed, tests pass.", 1, rowsOf(bashMissing), nil), "knowledge_gap", true},
			{base("fix the failing test in parser.py", "Fixed, tests pass.", 1, rowsOf(bashMissing), nil), "verify_failed", true},
		}},
		{"admits unknown on a build goal, once", []call{
			{base("implement the parser", "I don't know how to do that.", 1, nil, nil), "knowledge_gap", true},
			{base("implement the parser", "I don't know how to do that.", 1, nil, nil), "done", false},
		}},
		{"admits unknown after a web search", []call{
			{base("implement the parser", "I don't know how to do that.", 1, rowsOf(map[string]any{"name": "web.search", "ok": true}), nil), "done", false},
		}},
		{"admits unknown in plan mode", []call{
			{base("implement the parser", "I don't know how to do that.", 1, nil, map[string]any{"plan_mode": true}), "done", false},
		}},
		{"delegate report is not a check", []call{
			{base("implement the parser", "All done.", 1, rowsOf(map[string]any{"name": "delegate", "ok": true}), nil), "delegate_unverified", true},
		}},
		{"unverified edit, no latch", []call{
			{base("fix the bug in the build script", "Fixed it.", 1, rowsOf(editOK), nil), "unverified", true},
			{base("fix the bug in the build script", "Fixed it.", 1, rowsOf(editOK), nil), "unverified", true},
		}},
		{"edit after green check", []call{
			{base("fix the bug in the build script", "Fixed it.", 2, rowsOf(bashGreen, editOK), nil), "unverified", true},
		}},
		{"engine flag false with tools run", []call{
			{base("fix the bug in the build script", "Fixed it.", 3, nil, map[string]any{"verify_seen": false}), "unverified", true},
		}},
		{"engine flag as string no", []call{
			{base("fix the bug in the build script", "Fixed it.", 3, nil, map[string]any{"verify_seen": "No"}), "unverified", true},
		}},
		{"engine flag true wins over batch", []call{
			{base("fix the bug in the build script", "Fixed it.", 3, rowsOf(editOK), map[string]any{"verify_seen": true}), "done", false},
		}},
		{"green check closes the build", []call{
			{base("fix the bug in the build script", "Fixed it, tests are green.", 2, rowsOf(bashGreen), map[string]any{"verify_seen": true}), "done", false},
		}},
		{"plan mode skips unverified", []call{
			{base("fix the bug in the build script", "Fixed it.", 1, rowsOf(editOK), map[string]any{"plan_mode": true}), "done", false},
		}},
		{"narrated promise, repeats", []call{
			{base("review the repo", "Let me review the code now.", 0, nil, nil), "agency_promise", true},
			{base("review the repo", "Let me review the code now.", 0, nil, nil), "agency_promise", true},
		}},
		{"promise in plan mode falls to nothing", []call{
			{base("review the repo", "Let me review the code now.", 0, nil, map[string]any{"plan_mode": true}), "done", false},
		}},
		{"long soft promise is a final, then wants_tools", []call{
			{base("review the repo", strings.Repeat("x", 480)+" let me fix", 0, nil, nil), "wants_tools", true},
		}},
		{"long hard promise still re-arms", []call{
			{base("review the repo", strings.Repeat("x", 600)+" activating skill", 0, nil, nil), "agency_promise", true},
		}},
		{"has_tool_calls skips promise", []call{
			{base("review the repo", "Let me review the code now.", 0, nil, map[string]any{"has_tool_calls": true}), "wants_tools", true},
		}},
		{"pseudo tool text skips promise", []call{
			{base("review the repo", `bash_exec("ls") let me review`, 0, nil, nil), "wants_tools", true},
		}},
		{"chat goal with promise text", []call{
			{base("hi", "Let me review the code now.", 0, nil, nil), "done", false},
		}},
		{"wants_tools fires once per turn", []call{
			{base("tell me about src/main.rs", "It is a main file.", 0, nil, nil), "wants_tools", true},
			{base("tell me about src/main.rs", "It is a main file.", 0, nil, nil), "done", false},
		}},
		{"wants_tools skipped when tools ran", []call{
			{base("tell me about src/main.rs", "It is a main file.", 1, rowsOf(map[string]any{"name": "read", "ok": true}), nil), "done", false},
		}},
		{"wants_tools skipped in plan mode", []call{
			{base("tell me about src/main.rs", "It is a main file.", 0, nil, map[string]any{"plan_mode": true}), "done", false},
		}},
		{"message key stands in for goal", []call{
			{map[string]any{"message": "tell me about src/main.rs", "text": "ok", "tool_count": 0}, "wants_tools", true},
		}},
	}
	for _, c := range cases {
		st := &ContinueState{}
		for i, k := range c.calls {
			cont, nudge, reason := decideContinue(parseContinueInput(k.in), st)
			if reason != k.reason || cont != k.cont {
				t.Errorf("%s call %d: got (%v, %s), want (%v, %s)", c.name, i, cont, reason, k.cont, k.reason)
			}
			if (nudge != "") != cont {
				t.Errorf("%s call %d: nudge %q with continue=%v", c.name, i, nudge, cont)
			}
		}
	}
}

func TestContinueNudgeTexts(t *testing.T) {
	// Recorded from react_policy.failed_verify_nudge / knowledge_gap_nudge.
	if got, want := failedVerifyNudge("NAME", intp(7)), "[Verify] `NAME` returned exit_code=7, so the work is not done yet. Read the failure, fix it with edit, then re-run the same command with bash."; got != want {
		t.Errorf("failed verify nudge: %q", got)
	}
	if got, want := failedVerifyNudge("NAME", nil), "[Verify] `NAME` returned a failed result, so the work is not done yet. Read the failure, fix it with edit, then re-run the same command with bash."; got != want {
		t.Errorf("failed verify nudge (no code): %q", got)
	}
	if got, want := knowledgeGapNudge("NAME"), "[Verify] `NAME` failed on something outside this repo (a missing module, an unknown command, or a missing page). Look it up once with web.search before editing. If that tool is off, say what you could not find."; got != want {
		t.Errorf("knowledge nudge: %q", got)
	}
	if got, want := knowledgeGapNudge(""), "[Verify] This looks like a fact outside the repo (a missing module, an unknown command, or a missing page). Look it up once with web.search before editing. If that tool is off, say what you could not find."; got != want {
		t.Errorf("knowledge nudge (anon): %q", got)
	}
	if nudgeAgency != "Do not only *say* you will use tools or activate a skill. Call tools now via the function-calling API (e.g. skill.activate, glob, grep, read). Start the real review/work immediately." {
		t.Errorf("agency nudge: %q", nudgeAgency)
	}
}

func TestParseContinueInput(t *testing.T) {
	in := parseContinueInput(map[string]any{
		"goal": "", "message": "fix it", "text": "x", "session_id": "  s1 ", "tool_count": float64(3),
		"verify_seen": nil, "plan_mode": true, "has_tool_calls": 1.0,
	})
	if in.Goal != "fix it" || in.SessionID != "s1" || in.ToolCount != 3 || in.VerifySeen != nil || !in.PlanMode || !in.HasToolCalls {
		t.Errorf("parsed %+v", in)
	}
	if got := parseContinueInput(map[string]any{"verify_seen": 0.0}).VerifySeen; got != nil {
		t.Errorf("a non-bool, non-string verify_seen must be neither True nor False in Python")
	}
}
