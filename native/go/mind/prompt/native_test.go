package prompt

import (
	"strings"
	"testing"
)

type fakeSources struct{ memory, plan, todos string }

func (f fakeSources) MemoryBlock(string, string) string { return f.memory }
func (f fakeSources) ActivePlan(string) string          { return f.plan }
func (f fakeSources) Todos(string) string               { return f.todos }

func nativeCase(t *testing.T, name string) (caseEnv, map[string]any) {
	t.Helper()
	for _, f := range loadFixtures(t) {
		if f.Case != name {
			continue
		}
		ce := setupCase(t, f)
		for _, step := range f.Steps {
			if step.Op == OpAssemble {
				in, _ := ce.ph.fillAny(step.Input).(map[string]any)
				return ce, in
			}
		}
	}
	t.Fatalf("no fixture %q with an assemble step", name)
	return caseEnv{}, nil
}

func sectionNames(n Native) (sys, ctx []string) {
	inCtx := false
	for _, s := range n.Sections {
		if s.Name == ContextMarker {
			inCtx = true
		}
		if inCtx {
			ctx = append(ctx, s.Name)
		} else {
			sys = append(sys, s.Name)
		}
	}
	return sys, ctx
}

func TestNativeSystemIsTheSameForEveryMessageOfASession(t *testing.T) {
	ce, in := nativeCase(t, "empty_hello")
	ce.env.Sources = fakeSources{memory: "Partner memory:\n- [owner] (life) My dog is called Biscuit"}

	in["message"] = "hello"
	first, err := AssembleNative(ce.env, in)
	if err != nil {
		t.Fatal(err)
	}
	in["message"] = "please refactor the parser in src/parse.go and run the tests"
	second, err := AssembleNative(ce.env, in)
	if err != nil {
		t.Fatal(err)
	}
	if first.System != second.System {
		t.Fatalf("the system prompt changed with the message, so no provider can cache it:\n%q\nvs\n%q", first.System, second.System)
	}
	if first.Context == second.Context {
		t.Fatal("the context note should differ: only the first message is small talk")
	}
	// What changes turn to turn must not sit in the system prompt.
	for _, volatile := range []string{"Bond: rapport", "Top-level:", "My dog is called Biscuit", "[Turn context"} {
		if strings.Contains(first.System, volatile) {
			t.Errorf("system prompt holds per-turn text %q", volatile)
		}
	}
	for _, want := range []string{"[Turn context", "Bond: rapport", "My dog is called Biscuit", "Default working directory"} {
		if !strings.Contains(first.Context, want) {
			t.Errorf("context note lacks %q:\n%s", want, first.Context)
		}
	}
	if !strings.HasSuffix(first.Context, "Reply to the person. Be brief.") {
		t.Errorf("small talk in a working mode asks for a brief reply, got tail %q", first.Context[max(0, len(first.Context)-80):])
	}
	if strings.Contains(second.Context, "Be brief.") {
		t.Error("a work request must not be told to be brief")
	}
	sys, ctx := sectionNames(first)
	for _, name := range []string{"system_prompt", "runtime_info", "isolation", "contract", "builder", "approvals"} {
		if !hasName(sys, name) {
			t.Errorf("system sections %v lack %q", sys, name)
		}
	}
	for _, name := range []string{"soul", "workspace", "memory", "skills"} {
		if !hasName(ctx, name) {
			t.Errorf("context sections %v lack %q", ctx, name)
		}
	}
	if first.Goal != "hello" {
		t.Errorf("goal = %q", first.Goal)
	}
}

func TestNativeChatModeLeavesTheWorkingRulesOut(t *testing.T) {
	ce, in := nativeCase(t, "empty_hello")
	ce.env.Sources = fakeSources{plan: "## Active task plan\n# Plan: x", todos: "## Active checklist\n- [ ] `a` b"}
	in["message"] = "open the browser and click the login button"

	build, err := AssembleNative(ce.env, in)
	if err != nil {
		t.Fatal(err)
	}
	in["chat_mode"] = true
	chat, err := AssembleNative(ce.env, in)
	if err != nil {
		t.Fatal(err)
	}
	bsys, bctx := sectionNames(build)
	csys, cctx := sectionNames(chat)
	for _, name := range []string{"builder", "self_config", "durable_memory"} {
		if !hasName(bsys, name) || hasName(csys, name) {
			t.Errorf("%q belongs to working modes only: build=%v chat=%v", name, bsys, csys)
		}
	}
	for _, name := range []string{"active_plan", "todos", "computer_use"} {
		if !hasName(bctx, name) || hasName(cctx, name) {
			t.Errorf("%q belongs to working modes only: build=%v chat=%v", name, bctx, cctx)
		}
	}
}

func TestNativeLocalModelGetsOneSlimSystem(t *testing.T) {
	ce, in := nativeCase(t, "local_binding")
	n, err := AssembleNative(ce.env, in)
	if err != nil {
		t.Fatal(err)
	}
	if n.Context != "" {
		t.Errorf("a local model has no separate context note, got %q", n.Context)
	}
	if !strings.HasPrefix(n.System, "You are a local assistant on this PC") {
		t.Errorf("slim system = %.120q", n.System)
	}
	if len(n.Sections) != 1 || n.Sections[0].Name != "local_slim_system" {
		t.Errorf("sections = %+v", n.Sections)
	}
}

func hasName(list []string, want string) bool {
	for _, s := range list {
		if s == want {
			return true
		}
	}
	return false
}
