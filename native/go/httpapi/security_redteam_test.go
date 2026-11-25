package httpapi

import (
	"strings"
	"testing"

	"github.com/AhmiDarrow/RemedyAI/native/go/cognition"
)

func TestSettingsOwnerMoment(t *testing.T) {
	cases := []struct {
		tool, input string
		want        bool
	}{
		{"house_rules", `{"action":"propose","patch":{"llm_base_url":"https://x.example"},"confirm":true}`, true},
		{"house_rules", `{"action":"propose","patch":{"llm_api_key":"k"}}`, true},
		{"house_rules", `{"action":"propose","patch":{"approval_mode":"full"},"confirm":true}`, true},
		{"house_rules", `{"action":"confirm"}`, true},
		{"house_rules", `{"action":"propose","patch":{"persona":"concise"}}`, false},
		{"house_rules", `{"action":"status"}`, false},
		{"house_rules", `{"action":"record","answers":{"llm_provider":"x"}}`, false}, // recorded, applied by confirm
		{"settings.patch", `{"patch":{"approval_mode":"full"},"confirm":true}`, true},
		{"settings.patch", `{"patch":{"persona":"concise"}}`, false},
		{"settings.patch", `{"patch":`, true}, // unreadable: ask
		{"bash", `{"command":"ls"}`, false},
	}
	for _, c := range cases {
		call := cognition.ToolCall{Name: c.tool, Input: []byte(c.input)}
		if got := settingsOwnerMoment(call); got != c.want {
			t.Errorf("%s %s: got %v, want %v", c.tool, c.input, got, c.want)
		}
		if c.want && !approvalIsSensitive(call) {
			t.Errorf("%s %s: must queue as a one-shot owner approval", c.tool, c.input)
		}
	}
}

func TestLongCallsWithSameStartHaveDifferentApprovalKeys(t *testing.T) {
	head := `{"command":"` + strings.Repeat("a", toolCommandPreviewLimit)
	a := toolCommandPreview(cognition.ToolCall{Name: "bash", Input: []byte(head + `; echo one"}`)})
	b := toolCommandPreview(cognition.ToolCall{Name: "bash", Input: []byte(head + `; rm -rf two"}`)})
	if a == b {
		t.Fatal("two different long commands share one approval fingerprint")
	}
}

func TestPingProof(t *testing.T) {
	if pingProof("", "0123456789abcdef") != "" || pingProof("tok", "short") != "" {
		t.Fatal("no proof without a token or with a short nonce")
	}
	a, b := pingProof("tok", "0123456789abcdef"), pingProof("other", "0123456789abcdef")
	if len(a) != 64 || a == b {
		t.Fatalf("proof must depend on the token: %q %q", a, b)
	}
}
