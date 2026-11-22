package httpapi

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strconv"
	"strings"
)

// interopAlwaysApproval never runs for a peer without the owner, even when
// the peer is listed with the permission and is DID-verified.
var interopAlwaysApproval = map[string]bool{
	"spend": true, "transact": true, "send": true, "communicate": true,
	"delete": true, "irreversible": true,
}

// interopGatedAllowed is what an unverified or unknown peer may attempt
// under the default gated policy (unknown still queues for the owner).
var interopGatedAllowed = map[string]bool{
	"ask": true, "task_status": true, "share_context": true,
}

func interopNormalizeAction(action string) string {
	raw := strings.ToLower(strings.TrimSpace(action))
	if raw == "" {
		return "ask"
	}
	switch raw {
	case "transact", "pay", "money":
		return "spend"
	case "communicate", "message":
		return "send"
	case "remove", "rm":
		return "delete"
	}
	return raw
}

type interopDecision struct {
	Action  string
	Outcome string // allow | deny | need_approval
	Reason  string
	Trust   string
}

func interopDecide(action string, peer map[string]any, trust, policy string) interopDecision {
	act := interopNormalizeAction(action)
	policy = strings.ToLower(strings.TrimSpace(policy))
	if policy != "open" && policy != "gated" && policy != "verified" {
		policy = "gated"
	}
	if trust == "" {
		trust = "unverified"
	}
	if interopAlwaysApproval[act] {
		return interopDecision{act, "need_approval", "money, send, and delete always need the owner", trust}
	}
	if peer != nil {
		if enabled, ok := peer["enabled"].(bool); ok && !enabled {
			return interopDecision{act, "deny", "disabled peer", trust}
		}
		// TOML / JSON may store enabled as a string or omit it (default true).
		if raw, ok := peer["enabled"]; ok && raw != nil {
			if b, ok := interopTruthy(raw); ok && !b {
				return interopDecision{act, "deny", "disabled peer", trust}
			}
		}
	}
	if peer == nil {
		if policy == "verified" {
			return interopDecision{act, "deny", "trust policy requires a verified peer", trust}
		}
		if policy == "open" && (interopGatedAllowed[act] || act == "delegate") {
			return interopDecision{act, "allow", "open policy, unknown peer", trust}
		}
		if interopGatedAllowed[act] {
			return interopDecision{act, "need_approval", "unknown peer; queued for the owner", trust}
		}
		return interopDecision{act, "deny", "unknown peer", trust}
	}
	perms := map[string]bool{}
	if raw, ok := peer["permissions"].([]any); ok {
		for _, p := range raw {
			if s, ok := p.(string); ok {
				perms[strings.ToLower(strings.TrimSpace(s))] = true
			}
		}
	}
	if !perms[act] && act != "task_status" {
		return interopDecision{act, "deny", "peer is not permitted to " + act, trust}
	}
	if trust == "untrusted" {
		return interopDecision{act, "deny", "untrusted peer", trust}
	}
	if policy == "verified" && trust != "verified" {
		return interopDecision{act, "deny", "trust policy requires a verified peer", trust}
	}
	if policy == "gated" && trust != "verified" && !interopGatedAllowed[act] {
		return interopDecision{act, "need_approval", "unverified peers cannot do this without the owner", trust}
	}
	return interopDecision{act, "allow", "permitted", trust}
}

func interopTruthy(v any) (bool, bool) {
	switch n := v.(type) {
	case bool:
		return n, true
	case string:
		s := strings.ToLower(strings.TrimSpace(n))
		if s == "true" || s == "1" || s == "yes" || s == "on" {
			return true, true
		}
		if s == "false" || s == "0" || s == "no" || s == "off" {
			return false, true
		}
	}
	return false, false
}

func interopHopCount(meta map[string]any) int {
	if meta == nil {
		return 0
	}
	for _, key := range []string{"hop", "hopCount", "x-remedy-hop"} {
		if v, ok := meta[key]; ok && v != nil {
			switch n := v.(type) {
			case int:
				if n < 0 {
					return 0
				}
				return n
			case int64:
				if n < 0 {
					return 0
				}
				return int(n)
			case float64:
				if n < 0 {
					return 0
				}
				return int(n)
			case string:
				i, err := strconv.Atoi(strings.TrimSpace(n))
				if err == nil && i > 0 {
					return i
				}
			}
		}
	}
	return 0
}

func interopTrustPolicy(cfg ConfigMap) string {
	v := resolveAdvanced(advancedByKey()["interop_trust_policy"], cfg)
	s, _ := v.(string)
	s = strings.ToLower(strings.TrimSpace(s))
	if s == "" {
		return "gated"
	}
	return s
}

func (s *Server) interopLoadPeer(peerID string) map[string]any {
	peerID = strings.TrimSpace(peerID)
	if peerID == "" {
		return nil
	}
	path := filepath.Join(s.interopDir(), "peers.json")
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil
	}
	var wrap struct {
		Peers map[string]map[string]any `json:"peers"`
	}
	if json.Unmarshal(raw, &wrap) != nil || wrap.Peers == nil {
		return nil
	}
	if peer, ok := wrap.Peers[peerID]; ok {
		return peer
	}
	return nil
}

// interopTrustLevel is a local, no-network assessment. A well-formed DID is
// unverified unless a tighter plug is wired later; no DID is also unverified.
// A malformed DID is untrusted.
func interopTrustLevel(peer, meta map[string]any) string {
	did := ""
	for _, src := range []map[string]any{meta, peer} {
		if src == nil {
			continue
		}
		for _, key := range []string{"did", "identity"} {
			if s, ok := src[key].(string); ok && strings.HasPrefix(strings.ToLower(strings.TrimSpace(s)), "did:") {
				did = strings.TrimSpace(s)
				break
			}
		}
		if did == "" {
			if s, ok := src["id"].(string); ok && strings.HasPrefix(strings.ToLower(strings.TrimSpace(s)), "did:") {
				did = strings.TrimSpace(s)
			}
		}
		if did != "" {
			break
		}
	}
	if did == "" {
		return "unverified"
	}
	// very small well-formed check: did:<method>:<idchar>
	lower := strings.ToLower(did)
	parts := strings.SplitN(lower, ":", 3)
	if len(parts) != 3 || parts[0] != "did" || parts[1] == "" || parts[2] == "" {
		return "untrusted"
	}
	for _, r := range parts[1] {
		if (r < 'a' || r > 'z') && (r < '0' || r > '9') {
			return "untrusted"
		}
	}
	return "unverified"
}
