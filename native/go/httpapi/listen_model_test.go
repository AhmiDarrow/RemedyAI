package httpapi

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/AhmiDarrow/RemedyAI/native/go/cognition"
	"github.com/AhmiDarrow/RemedyAI/native/go/providers"
)

func TestResolveListenModelFallsBackToScripted(t *testing.T) {
	home := t.TempDir()
	t.Setenv("REMEDY_HOME", home)
	InvalidateConfigCache()
	model := ResolveListenModel(home)
	ch, err := model.Stream(context.Background(), cognition.Turn{Goal: "hi", Iteration: 1})
	if err != nil {
		t.Fatal(err)
	}
	var b strings.Builder
	for ev := range ch {
		b.WriteString(ev.Text)
	}
	if b.String() != "Hello world" {
		t.Fatalf("got %q", b.String())
	}
}

func TestResolveProviderAPIKeyUsesXaiOAuth(t *testing.T) {
	home := t.TempDir()
	t.Setenv("REMEDY_HOME", home)
	t.Setenv("XAI_API_KEY", "")
	t.Setenv("REMEDY_XAI_API_KEY", "")
	InvalidateConfigCache()
	_ = os.MkdirAll(filepath.Join(home, "auth"), 0o700)
	exp := float64(time.Now().Unix()) + 3600
	creds := xaiCredentials{
		AuthMethod:  "oauth",
		AccessToken: "eyJhbGciOiJSUzI1NiJ9.e30.signature-not-real-for-unit-test",
		ExpiresAt:   &exp,
		TokenType:   "Bearer",
	}
	if err := saveXaiCredentials(home, creds); err != nil {
		t.Fatal(err)
	}
	got := resolveProviderAPIKey(nil, "xai", home)
	if got != creds.AccessToken {
		t.Fatalf("resolveProviderAPIKey xai oauth = %q", got)
	}
	ok, reason := classifyProviderConnection("xai", nil, home, nil, nil, false)
	if !ok || reason != "oauth_or_key" {
		t.Fatalf("classify xai = %v %q", ok, reason)
	}
	model := ResolveChatModel(home, "xai", "grok-4.5", "https://api.x.ai/v1")
	oc, ok := model.(*providers.OpenAICompat)
	if !ok || oc == nil {
		t.Fatalf("want OpenAICompat, got %T", model)
	}
	if oc.APIKey != creds.AccessToken {
		t.Fatalf("chat model key = %q", oc.APIKey)
	}
	if oc.Model != "grok-4.5" {
		t.Fatalf("chat model id = %q", oc.Model)
	}
}

func TestResolveChatModelFallsBackFromUncredentialedBind(t *testing.T) {
	home := t.TempDir()
	t.Setenv("REMEDY_HOME", home)
	t.Setenv("XAI_API_KEY", "")
	t.Setenv("REMEDY_XAI_API_KEY", "")
	InvalidateConfigCache()
	_ = os.MkdirAll(filepath.Join(home, "auth"), 0o700)
	cfgPath := filepath.Join(home, "config.toml")
	if err := os.WriteFile(cfgPath, []byte("llm_provider = \"xai\"\nllm_model = \"grok-4.5\"\nllm_base_url = \"https://api.x.ai/v1\"\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	InvalidateConfigCache()
	exp := float64(time.Now().Unix()) + 3600
	creds := xaiCredentials{
		AuthMethod:  "oauth",
		AccessToken: "eyJhbGciOiJSUzI1NiJ9.e30.fallback-bind-token",
		ExpiresAt:   &exp,
		TokenType:   "Bearer",
	}
	if err := saveXaiCredentials(home, creds); err != nil {
		t.Fatal(err)
	}
	// Session pinned to deepseek with no key must not hard-fail — use xAI OAuth.
	model := ResolveChatModel(home, "deepseek", "deepseek-chat", "")
	oc, ok := model.(*providers.OpenAICompat)
	if !ok || oc == nil {
		t.Fatalf("want OpenAICompat fallback, got %T", model)
	}
	if oc.APIKey != creds.AccessToken {
		t.Fatalf("fallback key = %q", oc.APIKey)
	}
	if !strings.Contains(oc.BaseURL, "api.x.ai") {
		t.Fatalf("fallback base = %q", oc.BaseURL)
	}
	if oc.Model != "grok-4.5" {
		t.Fatalf("fallback model = %q", oc.Model)
	}
}

func TestNormalizeLLMSettingsIncludesPoe(t *testing.T) {
	prov, model, base := normalizeLLMSettings("poe", "", "")
	if prov != "poe" {
		t.Fatalf("prov = %q", prov)
	}
	if model != "assistant" {
		t.Fatalf("model = %q", model)
	}
	if base != "https://api.poe.com/v1" {
		t.Fatalf("base = %q (must not fall through to custom :5001)", base)
	}
}

func TestTryOpenAICompatPoeUsesCatalogURL(t *testing.T) {
	home := t.TempDir()
	t.Setenv("REMEDY_HOME", home)
	t.Setenv("POE_API_KEY", "sk-poe-unit-test-not-real")
	InvalidateConfigCache()
	model := ResolveChatModel(home, "poe", "assistant", "")
	oc, ok := model.(*providers.OpenAICompat)
	if !ok || oc == nil {
		t.Fatalf("want OpenAICompat for poe, got %T", model)
	}
	if !strings.Contains(oc.BaseURL, "api.poe.com") {
		t.Fatalf("poe base = %q", oc.BaseURL)
	}
	if strings.Contains(oc.BaseURL, "5001") {
		t.Fatalf("poe must not dial localhost:5001, got %q", oc.BaseURL)
	}
}

func TestTryOpenAICompatSkipsDownLocalProvider(t *testing.T) {
	home := t.TempDir()
	t.Setenv("REMEDY_HOME", home)
	t.Setenv("XAI_API_KEY", "")
	t.Setenv("REMEDY_XAI_API_KEY", "")
	InvalidateConfigCache()
	_ = os.MkdirAll(filepath.Join(home, "auth"), 0o700)
	cfgPath := filepath.Join(home, "config.toml")
	if err := os.WriteFile(cfgPath, []byte("llm_provider = \"xai\"\nllm_model = \"grok-4.5\"\nllm_base_url = \"https://api.x.ai/v1\"\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	InvalidateConfigCache()
	exp := float64(time.Now().Unix()) + 3600
	creds := xaiCredentials{
		AuthMethod:  "oauth",
		AccessToken: "eyJhbGciOiJSUzI1NiJ9.e30.local-skip-token",
		ExpiresAt:   &exp,
		TokenType:   "Bearer",
	}
	if err := saveXaiCredentials(home, creds); err != nil {
		t.Fatal(err)
	}
	// Ollama is credential-ready without a key, but nothing listens on :11434 —
	// ResolveChatModel must skip it and fall back to xAI OAuth.
	model := ResolveChatModel(home, "ollama", "llama3", "http://127.0.0.1:11434/v1")
	oc, ok := model.(*providers.OpenAICompat)
	if !ok || oc == nil {
		t.Fatalf("want OpenAICompat fallback from down ollama, got %T", model)
	}
	if !strings.Contains(oc.BaseURL, "api.x.ai") {
		t.Fatalf("expected xAI fallback, got base %q", oc.BaseURL)
	}
}

func TestIsLocalURL(t *testing.T) {
	if !isLocalURL("http://127.0.0.1:11434/v1") {
		t.Fatal("127.0.0.1 should be local")
	}
	if !isLocalURL("http://localhost:8080/v1") {
		t.Fatal("localhost should be local")
	}
	if isLocalURL("https://api.poe.com/v1") {
		t.Fatal("poe.com must not be local")
	}
	if isLocalURL("https://api.openai.com/v1") {
		t.Fatal("openai.com must not be local")
	}
}

func TestIsProviderUnusableError(t *testing.T) {
	cases := []struct {
		err  error
		want bool
	}{
		{nil, false},
		{errors.New("openai-compat HTTP 402: requires an active Poe subscription"), true},
		{errors.New("openai-compat HTTP 401: invalid api key"), true},
		{errors.New("openai-compat HTTP 403: forbidden"), true},
		{errors.New("openai-compat HTTP 429: rate limit"), false},
		{errors.New("openai-compat HTTP 500: boom"), false},
		{errors.New("payment required"), true},
	}
	for _, c := range cases {
		if got := isProviderUnusableError(c.err); got != c.want {
			t.Fatalf("isProviderUnusableError(%v) = %v want %v", c.err, got, c.want)
		}
	}
}

func TestResolveChatModelExcludesProvider(t *testing.T) {
	home := t.TempDir()
	t.Setenv("REMEDY_HOME", home)
	t.Setenv("XAI_API_KEY", "")
	t.Setenv("REMEDY_XAI_API_KEY", "")
	t.Setenv("POE_API_KEY", "sk-poe-unit-test-not-real")
	InvalidateConfigCache()
	_ = os.MkdirAll(filepath.Join(home, "auth"), 0o700)
	cfgPath := filepath.Join(home, "config.toml")
	if err := os.WriteFile(cfgPath, []byte("llm_provider = \"xai\"\nllm_model = \"grok-4.5\"\nllm_base_url = \"https://api.x.ai/v1\"\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	InvalidateConfigCache()
	exp := float64(time.Now().Unix()) + 3600
	creds := xaiCredentials{
		AuthMethod:  "oauth",
		AccessToken: "eyJhbGciOiJSUzI1NiJ9.e30.exclude-token",
		ExpiresAt:   &exp,
		TokenType:   "Bearer",
	}
	if err := saveXaiCredentials(home, creds); err != nil {
		t.Fatal(err)
	}
	// Excluding poe with empty bind should land on xAI (global), not Poe.
	model := resolveChatModel(home, "poe", "assistant", "", "poe")
	oc, ok := model.(*providers.OpenAICompat)
	if !ok || oc == nil {
		t.Fatalf("want OpenAICompat, got %T", model)
	}
	if !strings.Contains(oc.BaseURL, "api.x.ai") {
		t.Fatalf("exclude poe should yield xAI, got %q", oc.BaseURL)
	}
}
