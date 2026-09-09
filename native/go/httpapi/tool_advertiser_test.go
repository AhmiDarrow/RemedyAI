package httpapi

import (
	"context"
	"strings"
	"testing"

	"github.com/AhmiDarrow/RemedyAI/native/go/cognition"
	"github.com/AhmiDarrow/RemedyAI/native/go/providers"
)

func TestAdvertiseToolSurfaceReachesBothAdapters(t *testing.T) {
	r := NewCognitionTurnRunner(&cognition.ScriptedModel{})
	want := len(r.ModelVisibleToolIDs())
	if want == 0 {
		t.Fatalf("registry advertises no model-visible tools")
	}

	oc := &providers.OpenAICompat{BaseURL: "https://api.example.com/v1", Model: "x"}
	r.advertiseToolSurface(oc, false)
	if len(oc.Tools) != want {
		t.Fatalf("OpenAI surface = %d, want %d", len(oc.Tools), want)
	}
	if oc.ToolNameMap == nil {
		t.Fatalf("OpenAI adapter needs the advertised→ABI map")
	}

	claude := &providers.Anthropic{Model: "claude-opus-5", APIKey: "k"}
	r.advertiseToolSurface(claude, false)
	if got := claude.AdvertisedToolCount(); got != want {
		t.Fatalf("Anthropic surface = %d, want %d", got, want)
	}
}

func TestAdvertiseToolSurfaceKeepsCloudWindowOnCodingPack(t *testing.T) {
	r := NewCognitionTurnRunner(&cognition.ScriptedModel{})
	cloud := &providers.OpenAICompat{BaseURL: "https://api.example.com/v1", Model: "x"}
	r.advertiseToolSurface(cloud, false)
	full := len(cloud.Tools)
	r.advertiseToolSurface(cloud, true)
	if len(cloud.Tools) >= full {
		t.Fatalf("coding pack did not narrow the surface: %d → %d", full, len(cloud.Tools))
	}
	if cloud.LocalFit || cloud.NCtx != 0 {
		t.Fatalf("a cloud model must not be fitted to a local window: %#v", cloud)
	}

	local := &providers.OpenAICompat{BaseURL: "http://127.0.0.1:8741/v1", Model: "x"}
	r.advertiseToolSurface(local, false)
	if !local.LocalFit || local.ContextWindow() != providers.LocalContextWindow {
		t.Fatalf("a loopback model must keep the local fitter: %#v", local)
	}
	if len(local.Tools) >= full {
		t.Fatalf("a loopback model must get the coding pack: %d", len(local.Tools))
	}
}

func TestAdvertiseToolSurfaceSkipsTheVisionHelper(t *testing.T) {
	r := NewCognitionTurnRunner(&cognition.ScriptedModel{})
	helper := &providers.OpenAICompat{BaseURL: "http://127.0.0.1:8740/v1", Model: "vision-decoder"}
	r.advertiseToolSurface(helper, false)
	if len(helper.Tools) != 0 {
		t.Fatalf("the local VLM must not be shown the tool surface: %d", len(helper.Tools))
	}
}

func TestAdvertiseToolSurfaceIgnoresNonAdvertisers(t *testing.T) {
	r := NewCognitionTurnRunner(&cognition.ScriptedModel{})
	// A model that cannot carry tools is not an error — it just gets none.
	r.advertiseToolSurface(&cognition.ScriptedModel{}, false)
	r.advertiseToolSurface(nil, false)
}

func TestAnthropicCatalogIdsAreCurrent(t *testing.T) {
	meta, ok := providerCatalog["anthropic"]
	if !ok {
		t.Fatalf("no anthropic catalog entry")
	}
	ids := make([]string, 0, len(meta.Models))
	for _, m := range meta.Models {
		ids = append(ids, m.ID)
	}
	joined := strings.Join(ids, ",")
	for _, want := range []string{
		"claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5",
		"claude-opus-4-8", "claude-sonnet-4-6", "claude-fable-5-1",
	} {
		if !strings.Contains(joined, want) {
			t.Fatalf("catalog is missing %s: %s", want, joined)
		}
	}
	if defaultModelForProvider("anthropic") != ids[0] {
		t.Fatalf("the default model %q should be the first catalog id %q",
			defaultModelForProvider("anthropic"), ids[0])
	}
}

// stubModel emits a fixed event sequence.
type stubModel struct{ events []cognition.ModelEvent }

func (stubModel) ContextWindow() int { return 0 }

func (m stubModel) Stream(context.Context, cognition.Turn) (<-chan cognition.ModelEvent, error) {
	ch := make(chan cognition.ModelEvent, len(m.events))
	for _, ev := range m.events {
		ch <- ev
	}
	close(ch)
	return ch, nil
}

func TestNonCompatToolNamesSurviveTheResolvingModel(t *testing.T) {
	// runEngine wraps every model in resolvingModel with the OpenAI adapter's
	// name map — nil for a native adapter. A nil map must leave the Tool ABI
	// id alone: approvals are fingerprinted on it.
	var nilCompat *providers.OpenAICompat
	inner := stubModel{events: []cognition.ModelEvent{
		{ToolCall: &cognition.ToolCall{ID: "toolu_1", Name: "workspace.read", Advertised: "workspace_read"}},
		{Done: true},
	}}
	model := &resolvingModel{inner: inner, resolve: nilCompat.ResolveToolName}

	ch, err := model.Stream(context.Background(), cognition.Turn{})
	if err != nil {
		t.Fatal(err)
	}
	var call *cognition.ToolCall
	for ev := range ch {
		if ev.ToolCall != nil {
			call = ev.ToolCall
		}
	}
	if call == nil || call.Name != "workspace.read" || call.Advertised != "workspace_read" {
		t.Fatalf("tool identity mangled: %+v", call)
	}
}
