// Package houserules is core/house_rules.py: the owner's house rules, the
// first-run conversation, talk-to-change, audit, and rollback. Dispatch is the
// tool entry for both `house_rules` and `setup.session`.
//
// Files under Env.Home: house_rules.toml, setup_session.json,
// house_rules_pending.json, house_rules_prev.json, house_rules_audit.jsonl.
// They keep the shapes Python writes, so either side can read the other's.
package houserules

import (
	"fmt"
	"sort"
	"strings"
	"sync"
	"time"
)

// Env is what a call needs besides its input.
type Env struct {
	Home   string                           // Remedy home directory
	Now    func() time.Time                 // nil means time.Now
	Config map[string]any                   // parsed config.toml (read-only view)
	Apply  func(patch map[string]any) error // applies confirmed settings; nil means "cannot apply here"
	Origin string                           // who is calling; empty falls back to the input's caller_origin

	// StoreSecret writes a provider API key to the secure store. Optional: nil
	// means keys cannot be stored here, and they are then not recorded as set.
	StoreSecret func(provider, key string) error
}

// Apply receives a config.toml merge patch: root keys at the top level and
// [advanced] keys under "advanced". Values are never secrets.

var mu sync.Mutex // _LOCK

type run struct {
	env    Env
	cfg    map[string]any // working copy of Config, updated after each Apply
	origin string
}

type failure struct{ err error }

func check(err error) {
	if err != nil {
		panic(failure{err})
	}
}

var errNoApply = fmt.Errorf("cannot apply settings here")

func (r *run) now() string {
	t := time.Now()
	if r.env.Now != nil {
		t = r.env.Now()
	}
	return t.UTC().Format("2006-01-02T15:04:05Z")
}

func fail(msg string) map[string]any { return map[string]any{"ok": false, "error": msg} }

// Dispatch is house_rules.dispatch: one action in, one JSON-shaped result out.
// The result is always valid to relay; a non-nil error is only the cause behind
// Python's generic "house rules action failed" result, for the caller's log.
func Dispatch(env Env, input map[string]any) (res map[string]any, err error) {
	mu.Lock()
	defer mu.Unlock()
	r := &run{env: env, cfg: copyMap(env.Config), origin: pyStrip(pyOr(input["caller_origin"]))}
	if env.Origin != "" {
		r.origin = pyStrip(env.Origin)
	}
	defer func() {
		if p := recover(); p != nil {
			f, ok := p.(failure)
			if !ok {
				panic(p)
			}
			res, err = fail("house rules action failed"), f.err
		}
	}()
	action := strings.ToLower(pyStrip(pyStr(orDefault(input["action"], "status"))))
	mutating := set("record", "skip_all", "confirm", "propose", "decline", "rollback", "revisit", "set_mode",
		"finalize", "update_offer_go", "update_offer_later", "update_offer_accept", "update_offer_dismiss")
	if mutating[action] && !canChange(r.origin) {
		return fail(errPeers), nil
	}
	switch action {
	case "status", "get", "":
		out := r.publicSession()
		out["ok"] = true
		return out, nil
	case "summarize":
		lines := r.summaryLines(nil, nil)
		return map[string]any{"ok": true, "summary": strings.Join(lines, "\n"), "lines": toAny(lines), "session": r.publicSession()}, nil
	case "record":
		answers, ok := firstTruthy(input["answers"], input["patch"]).(map[string]any)
		if firstTruthy(input["answers"], input["patch"]) != nil && !ok {
			return fail("answers must be an object"), nil
		}
		skipped, _ := orList(input["skipped"])
		return r.recordAnswers(answers, skipped), nil
	case "skip_all":
		return r.skipAll(), nil
	case "confirm":
		if r.loadPending() != nil {
			return r.confirmPending(input["pending_id"]), nil
		}
		return r.confirmOnboarding("onboarding"), nil
	case "propose":
		patch, ok := firstTruthy(input["patch"], input["answers"]).(map[string]any)
		if firstTruthy(input["patch"], input["answers"]) != nil && !ok {
			return fail("patch must be an object"), nil
		}
		return r.propose(patch, truthy(input["confirm"]), pyStr(orDefault(input["source"], "chat"))), nil
	case "decline":
		return r.declinePending(), nil
	case "rollback":
		return r.rollback(), nil
	case "revisit":
		return r.revisit(), nil
	case "set_mode":
		wanted := modeConversation
		if strings.ToLower(pyStrip(pyOr(input["mode"]))) == modeTraditional {
			wanted = modeTraditional
		}
		s := r.loadSession()
		s["mode"] = wanted
		r.saveSession(s)
		return map[string]any{"ok": true, "session": r.publicSession()}, nil
	case "update_draft":
		patch, ok := firstTruthy(input["patch"], input["answers"]).(map[string]any)
		if firstTruthy(input["patch"], input["answers"]) != nil && !ok {
			return fail("patch must be an object"), nil
		}
		skipped, isList := orList(input["skipped"])
		if !isList {
			skipped = nil
		}
		return r.updateDraft(patch, skipped), nil
	case "offer_prompt":
		return map[string]any{"ok": true, "prompt": offerPrompt()}, nil
	case "update_offer_go", "update_offer_later", "update_offer_accept", "update_offer_dismiss":
		return r.resolveUpdateOffer(action), nil
	}
	return fail("unknown action " + action), nil
}

func orDefault(v any, def any) any {
	if truthy(v) {
		return v
	}
	return def
}

// firstTruthy is `a or b`; nil when both are falsy (Python would use {}).
func firstTruthy(vs ...any) any {
	for _, v := range vs {
		if truthy(v) {
			return v
		}
	}
	return nil
}

// orList is `list(v or [])` when v is a list, plus whether it was one.
func orList(v any) ([]any, bool) {
	l, ok := asList(v)
	if !ok {
		return nil, false
	}
	return append([]any{}, l...), true
}

func toAny(ss []string) []any { return asAnyList(ss) }

// --- config view ----------------------------------------------------------

func (r *run) knob(key string) (any, bool) {
	adv, _ := r.cfg["advanced"].(map[string]any)
	v, ok := adv[key]
	return v, ok && v != nil
}

// knobBool is organism_knobs.resolve_bool.
func (r *run) knobBool(key string, def bool) bool {
	v, ok := r.knob(key)
	if !ok {
		return def
	}
	if s, ok := v.(string); ok {
		switch strings.ToLower(pyStrip(s)) {
		case "1", "true", "yes", "on":
			return true
		}
		return false
	}
	return truthy(v)
}

// permission is _permission.
func (r *run) permission() string {
	v, ok := r.knob("house_rules_permission")
	if !ok {
		return permConfirmSensitive
	}
	s := strings.ToLower(pyStrip(pyStr(v)))
	if s == permConfirmSensitive || s == permConfirmAll || s == permPlain {
		return s
	}
	return permConfirmSensitive
}

// configSnapshot is _config_snapshot: the settings house rules can touch.
func (r *run) configSnapshot() map[string]any {
	keys := map[string]bool{}
	for k := range fieldByKey {
		keys[k] = true
	}
	for k := range sensitiveKeys {
		keys[k] = true
	}
	for k := range forwardAdvancedKeys {
		keys[k] = true
	}
	adv, _ := r.cfg["advanced"].(map[string]any)
	out := map[string]any{}
	for key := range keys {
		if isSecretKey(key) {
			continue
		}
		f, isField := fieldByKey[key]
		av, inAdv := adv[key]
		if cv, ok := r.cfg[key]; isField && f.advanced {
			if inAdv {
				out[key] = av
			}
		} else if ok {
			out[key] = cv
		} else if inAdv {
			out[key] = av
		}
	}
	return scrubMap(out)
}

// storeSecret is _store_secret; it reports whether the key really was stored.
func (r *run) storeSecret(key, value, provider string) bool {
	if pyStrip(value) == "" || key != "llm_api_key" || r.env.StoreSecret == nil {
		return false
	}
	prov := pyStrip(provider)
	if prov == "" {
		prov = pyStr(orDefault(r.cfg["llm_provider"], "openai"))
	}
	return r.env.StoreSecret(prov, value) == nil
}

// applyConfigPatch is apply_config_patch (lines 774-816). It returns the
// from/to changes. A secret that could not be stored is left out of them.
func (r *run) applyConfigPatch(patch map[string]any) (changed map[string]any, notStored []string) {
	clean := map[string]any{}
	secrets := map[string]any{}
	for k, v := range patch {
		if isSecretKey(k) {
			secrets[k] = v
		} else {
			clean[k] = v
		}
	}
	cfg := r.cfg
	out := map[string]any{}
	if len(cfg) == 0 {
		cfg = map[string]any{"home_dir": r.env.Home}
		out["home_dir"] = r.env.Home
	}
	adv, _ := cfg["advanced"].(map[string]any)
	adv = copyMap(adv)
	outAdv := map[string]any{}
	changed = map[string]any{}
	for _, key := range sortedKeys(clean) {
		value := clean[key]
		f, isField := fieldByKey[key]
		if (isField && f.advanced) || forwardAdvancedKeys[key] {
			if prev := adv[key]; !pyEq(prev, value) {
				changed[key] = map[string]any{"from": prev, "to": value}
			}
			adv[key], outAdv[key] = value, deepCopy(value)
		} else {
			if prev := cfg[key]; !pyEq(prev, value) {
				changed[key] = map[string]any{"from": prev, "to": value}
			}
			cfg[key], out[key] = value, deepCopy(value)
		}
	}
	if len(outAdv) > 0 {
		out["advanced"] = outAdv
	}
	if r.env.Apply == nil {
		check(errNoApply)
	}
	check(r.env.Apply(out))
	if len(adv) > 0 {
		cfg["advanced"] = adv
	}
	r.cfg = cfg
	provider := pyStr(orDefault(clean["llm_provider"], orDefault(cfg["llm_provider"], "")))
	for _, skey := range sortedKeys(secrets) {
		if r.storeSecret(skey, pyOr(secrets[skey]), provider) {
			changed[skey] = map[string]any{"from": nil, "to": "stored"}
		} else {
			notStored = append(notStored, skey)
		}
	}
	return changed, notStored
}

// --- state ------------------------------------------------------------------

func (r *run) snapshotPrev() {
	snap := map[string]any{"ts": r.now(), "rules": r.loadRules(), "config": r.configSnapshot()}
	check(writeJSON(r.path("house_rules_prev.json"), scrub(snap)))
}

func (r *run) setupCompleted() bool { return truthy(r.cfg["setup_completed"]) }

func (r *run) onboardingEnabled() bool { return r.knobBool("onboarding", true) }

func statusIn(s string, in ...string) bool {
	for _, x := range in {
		if s == x {
			return true
		}
	}
	return false
}

func (r *run) updateOfferAvailable() bool {
	if !r.onboardingEnabled() || !r.setupCompleted() {
		return false
	}
	session, rules := r.loadSession(), r.loadRules()
	seen := pyOr(orDefault(session["update_offer"], rules["update_offer"]))
	if seen == "accepted" || seen == "declined" {
		return false
	}
	status := pyOr(orDefault(rules["status"], session["status"]))
	if statusIn(status, statusComplete, statusSkipped) {
		return false
	}
	source := pyOr(orDefault(session["source"], rules["source"]))
	return !(truthy(session["onboarding_pending"]) && source != "revisit")
}

func (r *run) updateOfferPublic() map[string]any {
	return map[string]any{
		"available": r.updateOfferAvailable(), "id": updateOfferID, "title": updateOfferTitle,
		"body": updateOfferBody, "primary": updateOfferPrimary, "secondary": updateOfferSecondary,
	}
}

// publicSession is public_session (lines 1581-1630) with no mind status.
func (r *run) publicSession() map[string]any {
	session, rules := r.loadSession(), r.loadRules()
	var pending any
	if p := r.loadPending(); p != nil {
		pending = p
	}
	mode := orDefault(session["mode"], modeConversation)
	return map[string]any{
		"version": rulesVersion, "mode": mode,
		"status":             orDefault(session["status"], orDefault(rules["status"], statusPending)),
		"rules_status":       rules["status"],
		"onboarding_pending": truthy(session["onboarding_pending"]),
		"onboarding_enabled": r.onboardingEnabled(),
		"mind_ready":         false, "mind_failure": session["mind_failure"], "mind_progress": nil,
		"draft":            scrub(orMap(session["draft"])),
		"skipped":          listOf(session["skipped"]),
		"secrets_set":      listOf(session["secrets_set"]),
		"answers":          scrub(orMap(rules["answers"])),
		"pending":          pending,
		"setup_session_id": orDefault(session["setup_session_id"], ""),
		"questions":        questionsPublic(), "defaults": defaults(),
		"summary":      strings.Join(r.summaryLines(nil, nil), "\n"),
		"offer_prompt": offerPrompt(), "update_offer": r.updateOfferPublic(),
	}
}

func orMap(v any) map[string]any {
	if m, ok := v.(map[string]any); ok {
		return m
	}
	return map[string]any{}
}

func listOf(v any) []any {
	if l, ok := asList(v); ok {
		return append([]any{}, l...)
	}
	return []any{}
}

// summaryLines is summary_lines (lines 1152-1186).
func (r *run) summaryLines(answers map[string]any, skipped []any) []string {
	session, rules := r.loadSession(), r.loadRules()
	values := defaults()
	for k, v := range orMap(rules["answers"]) {
		values[k] = v
	}
	for k, v := range orMap(session["draft"]) {
		values[k] = v
	}
	if len(answers) > 0 {
		for k, v := range scrubMap(normalizePatch(answers)) {
			values[k] = v
		}
	}
	var src []any
	for _, c := range [][]any{skipped, listOf(session["skipped"]), listOf(rules["skipped"])} {
		if len(c) > 0 {
			src = c
			break
		}
	}
	skip := map[string]bool{}
	for _, x := range src {
		if !hashable(x) {
			check(fmt.Errorf("unhashable skipped item")) // Python: set() raises TypeError
		}
		if s, ok := x.(string); ok {
			skip[s] = true
		}
	}
	secretsSet := listOf(session["secrets_set"])
	var lines []string
	for _, q := range questions {
		var bits []string
		anySet := false
		for _, f := range q.fields {
			if f.secret {
				flag := false
				for _, s := range secretsSet {
					flag = flag || pyEq(s, f.key)
				}
				word := "not set"
				if flag {
					word = "saved in the secure store"
				}
				bits = append(bits, f.key+": "+word)
				anySet = anySet || flag
				continue
			}
			val, ok := values[f.key]
			if !ok {
				val = f.def
			}
			flag := "set"
			if pyEq(val, f.def) {
				flag = "default"
				if skip[q.id] {
					flag = "skipped (default)"
				}
			} else {
				anySet = true
			}
			bits = append(bits, fmt.Sprintf("%s: %s (%s)", f.key, pyStr(val), flag))
		}
		mark := "ok"
		if skip[q.id] && !anySet {
			mark = "skipped"
		}
		lines = append(lines, q.title+": "+strings.Join(bits, "; ")+" ["+mark+"]")
	}
	return lines
}

// --- draft and answers -------------------------------------------------------

// updateDraft is update_draft (lines 1065-1098).
func (r *run) updateDraft(patch map[string]any, skipped []any) map[string]any {
	if !canChange(r.origin) {
		return fail(errPeers)
	}
	session := r.loadSession()
	draft := copyMap(orMap(session["draft"]))
	stored := normalizePatch(patch)
	secretsSet := listOf(session["secrets_set"])
	var notStored []any
	for _, key := range sortedKeys(stored) {
		if !isSecretKey(key) {
			continue
		}
		val := stored[key]
		delete(stored, key)
		provider := pyStr(orDefault(stored["llm_provider"], orDefault(draft["llm_provider"], "")))
		if r.storeSecret(key, pyOr(val), provider) {
			have := false
			for _, s := range secretsSet {
				have = have || pyEq(s, key)
			}
			if !have {
				secretsSet = append(secretsSet, key)
			}
		} else {
			notStored = append(notStored, key)
		}
	}
	for k, v := range scrubMap(stored) {
		draft[k] = v
	}
	session["draft"], session["secrets_set"] = draft, secretsSet
	if len(skipped) > 0 {
		have := listOf(session["skipped"])
		for _, item := range skipped {
			found := false
			for _, h := range have {
				found = found || pyEq(h, item)
			}
			if !found {
				have = append(have, item)
			}
		}
		session["skipped"] = have
	}
	if session["status"] == statusPending {
		session["status"] = "in_progress"
	}
	r.saveSession(session)
	out := map[string]any{"ok": true, "session": r.publicSession()}
	if len(notStored) > 0 {
		out["secrets_not_stored"] = notStored // this host has no secure store to put them in
	}
	return out
}

func (r *run) recordAnswers(answers map[string]any, skipped []any) map[string]any {
	if !canChange(r.origin) {
		return fail(errPeers)
	}
	res := r.updateDraft(answers, skipped)
	if res["ok"] != true {
		return res
	}
	keys := sortedKeys(scrubMap(normalizePatch(answers)))
	r.appendAudit(map[string]any{
		"action": "record", "actor": "owner", "origin": r.origin, "source": "onboarding",
		"keys": toAny(keys), "skipped": listOf(skipped),
	})
	return res
}

func (r *run) skipAll() map[string]any {
	session := r.loadSession()
	applied := defaults()
	for k, v := range orMap(session["draft"]) {
		applied[k] = v
	}
	ids := make([]any, len(questions))
	for i, q := range questions {
		ids[i] = q.id
	}
	return r.finalize(applied, ids, "skip", statusSkipped)
}

func (r *run) confirmOnboarding(source string) map[string]any {
	session := r.loadSession()
	applied := defaults()
	for k, v := range orMap(session["draft"]) {
		applied[k] = v
	}
	return r.finalize(applied, listOf(session["skipped"]), source, statusComplete)
}

// finalize is _finalize (lines 1217-1290).
func (r *run) finalize(answers map[string]any, skipped []any, source, status string) map[string]any {
	r.snapshotPrev()
	clean := scrubMap(normalizePatch(answers))
	filled := defaults()
	for k, v := range clean {
		filled[k] = v
	}
	changed, _ := r.applyConfigPatch(filled)
	now := r.now()
	rules := r.loadRules()
	prov := copyMap(orMap(rules["provenance"]))
	skippedSet := map[string]bool{}
	for _, s := range skipped {
		if !hashable(s) {
			check(fmt.Errorf("unhashable skipped item"))
		}
		if str, ok := s.(string); ok {
			skippedSet[str] = true
		}
	}
	defs := defaults()
	for key, value := range filled {
		if isSecretKey(key) {
			continue
		}
		// Python compares the field key (not the question id) with the skipped ids.
		srcLabel := source
		if skippedSet[key] && pyEq(value, fieldByKey[key].def) {
			srcLabel = "skip"
		}
		prov[key] = map[string]any{
			"set_by": "owner", "set_at": now, "source": srcLabel,
			"skipped": skippedSet[key] && pyEq(value, defs[key]),
		}
	}
	rules["answers"], rules["provenance"] = filled, prov
	rules["skipped"] = copyList(skipped)
	rules["status"], rules["completed_at"], rules["source"] = status, now, source
	rules["update_offer"], rules["update_offer_id"] = "accepted", updateOfferID
	r.saveRules(rules)
	session := r.loadSession()
	session["draft"], session["skipped"] = copyMap(filled), copyList(skipped)
	session["status"], session["completed_at"], session["onboarding_pending"], session["source"] = status, now, false, source
	session["update_offer"], session["update_offer_id"] = "accepted", updateOfferID
	r.saveSession(session)
	// mark_setup_completed(extra={"setup_completed": True}); its home stretch
	// side effect is the caller's to wire.
	done := map[string]any{"setup_completed": true}
	if _, ok := r.cfg["home_dir"]; !ok {
		done["home_dir"] = strings.ReplaceAll(r.env.Home, "\\", "/")
	}
	if r.env.Apply == nil {
		check(errNoApply)
	}
	check(r.env.Apply(deepCopy(done).(map[string]any)))
	for k, v := range done {
		r.cfg[k] = v
	}
	r.appendAudit(map[string]any{
		"action": "finalize", "actor": "owner", "origin": r.origin, "source": source,
		"status": status, "changes": changed, "skipped": copyList(skipped),
	})
	return map[string]any{
		"ok": true, "status": status,
		"summary": strings.Join(r.summaryLines(filled, skipped), "\n"),
		"changes": scrub(changed), "session": r.publicSession(),
	}
}

func copyList(l []any) []any { return deepCopy(append([]any{}, l...)).([]any) }

// --- talk to change -----------------------------------------------------------

func (r *run) needsConfirmation(keys []string) bool {
	if r.permission() == permConfirmAll {
		return true
	}
	for _, k := range keys {
		if isSensitiveKey(k) {
			return true
		}
	}
	return false
}

func (r *run) diffAgainstCurrent(patch map[string]any) map[string]any {
	current := defaults()
	for k, v := range orMap(r.loadRules()["answers"]) {
		current[k] = v
	}
	for k, v := range r.configSnapshot() {
		current[k] = v
	}
	diff := map[string]any{}
	for key, value := range patch {
		if isSecretKey(key) {
			continue
		}
		if old := current[key]; !pyEq(old, value) {
			diff[key] = map[string]any{"from": old, "to": value}
		}
	}
	return diff
}

func formatDiff(diff map[string]any) string {
	if len(diff) == 0 {
		return "No changes."
	}
	var lines []string
	keys := sortedKeys(diff)
	sort.SliceStable(keys, func(i, j int) bool { return !isSecretKey(keys[i]) && isSecretKey(keys[j]) }) // Python adds secrets last
	for _, key := range keys {
		pair := orMap(diff[key])
		lines = append(lines, key+": "+pyRepr(pair["from"])+" → "+pyRepr(pair["to"]))
	}
	return strings.Join(lines, "\n")
}

func (r *run) propose(patch map[string]any, confirm bool, source string) map[string]any {
	normalized := normalizePatch(patch)
	visible := scrubMap(normalized)
	anySecret := false
	var secrets []string
	for k := range normalized {
		if isSecretKey(k) {
			anySecret = true
			secrets = append(secrets, k)
		}
	}
	if len(visible) == 0 && !anySecret {
		return fail("no recognized house-rules keys in the patch")
	}
	sort.Strings(secrets)
	keys := append(sortedKeys(visible), secrets...)
	wait := r.needsConfirmation(keys) && !confirm
	diff := r.diffAgainstCurrent(visible)
	if !wait {
		return r.applyChange(normalized, source)
	}
	var now time.Time = time.Now()
	if r.env.Now != nil {
		now = r.env.Now()
	}
	pending := map[string]any{
		"id": fmt.Sprintf("hr-%d", now.Unix()), "created_at": r.now(), "kind": "change",
		"keys": toAny(keys), "patch": visible, "diff": diff, "source": source, "origin": r.origin,
	}
	r.savePending(pending)
	r.appendAudit(map[string]any{
		"action": "propose", "actor": "owner", "origin": r.origin, "source": source,
		"keys": toAny(keys), "needs_confirmation": true,
	})
	return map[string]any{
		"ok": true, "needs_confirmation": true, "pending_id": pending["id"], "diff": diff,
		"summary": formatDiff(diff), "message": "This change needs your OK. Say yes to apply it, or no to cancel.",
	}
}

func (r *run) confirmPending(pendingID any) map[string]any {
	pending := r.loadPending()
	if pending == nil {
		return fail("no pending house-rules change")
	}
	if truthy(pendingID) && !pyEq(pending["id"], pendingID) {
		return fail("pending change id does not match")
	}
	if pending["kind"] == "onboarding_confirm" {
		r.savePending(nil)
		return r.confirmOnboarding("onboarding")
	}
	patch := copyMap(orMap(pending["patch"]))
	r.savePending(nil)
	return r.applyChange(patch, pyStr(orDefault(pending["source"], "chat")))
}

func (r *run) declinePending() map[string]any {
	pending := r.loadPending()
	r.savePending(nil)
	r.appendAudit(map[string]any{"action": "decline", "actor": "owner", "origin": r.origin})
	out := map[string]any{"ok": true, "declined": pending != nil}
	if pending != nil {
		out["pending_id"] = pending["id"] // Python's null is omitted: the schema says string
	}
	return out
}

func (r *run) rollback() map[string]any {
	prev := loadJSON(r.path("house_rules_prev.json"))
	if len(prev) == 0 {
		return fail("no previous house-rules snapshot to roll back to")
	}
	current := map[string]any{"ts": r.now(), "rules": r.loadRules(), "config": r.configSnapshot()}
	rules, ok := prev["rules"].(map[string]any)
	if !ok {
		rules = emptyRules()
	}
	bits, _ := prev["config"].(map[string]any)
	r.snapshotPrev()
	check(writeJSON(r.path("house_rules_prev.json"), scrub(current)))
	r.saveRules(rules)
	r.applyConfigPatch(scrubMap(bits))
	r.appendAudit(map[string]any{"action": "rollback", "actor": "owner", "origin": r.origin})
	return map[string]any{
		"ok": true, "rules": r.loadRules(), "summary": strings.Join(r.summaryLines(nil, nil), "\n"),
	}
}

func (r *run) revisit() map[string]any {
	session, rules := r.loadSession(), r.loadRules()
	draft := defaults()
	for k, v := range orMap(rules["answers"]) {
		draft[k] = v
	}
	for k, v := range orMap(session["draft"]) {
		draft[k] = v
	}
	session["draft"] = scrubMap(draft)
	session["mode"], session["onboarding_pending"] = modeConversation, true
	session["status"], session["source"] = statusPending, "revisit"
	r.saveSession(session)
	rules["status"] = statusPending
	r.saveRules(rules)
	r.appendAudit(map[string]any{"action": "revisit", "actor": "owner", "origin": r.origin})
	return map[string]any{
		"ok": true, "prompt": revisitPrompt(), "session": r.publicSession(), "questions": questionsPublic(),
	}
}

// applyChange is _apply_change (lines 1471-1518).
func (r *run) applyChange(patch map[string]any, source string) map[string]any {
	r.snapshotPrev()
	clean := normalizePatch(patch)
	changed, _ := r.applyConfigPatch(clean)
	now := r.now()
	rules := r.loadRules()
	answers := copyMap(orMap(rules["answers"]))
	prov := copyMap(orMap(rules["provenance"]))
	visible := scrubMap(clean)
	for k, v := range visible {
		answers[k] = v
		prov[k] = map[string]any{"set_by": "owner", "set_at": now, "source": source, "skipped": false}
	}
	rules["answers"], rules["provenance"] = answers, prov
	if !statusIn(pyStr(rules["status"]), statusComplete, statusSkipped, statusInherited) {
		rules["status"], rules["completed_at"] = statusComplete, now
	}
	r.saveRules(rules)
	session := r.loadSession()
	draft := copyMap(orMap(session["draft"]))
	for k, v := range visible {
		draft[k] = v
	}
	session["draft"] = draft
	r.saveSession(session)
	r.savePending(nil)
	r.appendAudit(map[string]any{
		"action": "apply", "actor": "owner", "origin": r.origin, "source": source, "changes": changed,
	})
	return map[string]any{
		"ok": true, "needs_confirmation": false, "changes": scrub(changed), "diff": scrub(changed),
		"summary": formatDiff(changed), "session": r.publicSession(),
	}
}

// resolveUpdateOffer is resolve_update_offer (lines 985-1044).
func (r *run) resolveUpdateOffer(choice string) map[string]any {
	session, rules := r.loadSession(), r.loadRules()
	already := pyOr(orDefault(session["update_offer"], rules["update_offer"]))
	if already == "accepted" || already == "declined" {
		return map[string]any{"ok": true, "choice": already, "already": true, "update_offer": r.updateOfferPublic()}
	}
	raw := strings.NewReplacer(" ", "_", "'", "").Replace(strings.ToLower(pyStrip(choice)))
	accepted := statusIn(raw, "go", "accepted", "lets_go", "letsgo", "update_offer_go")
	recorded := "declined"
	if accepted {
		recorded = "accepted"
	}
	now := r.now()
	session["update_offer"], session["update_offer_id"], session["update_offer_at"] = recorded, updateOfferID, now
	rules["update_offer"], rules["update_offer_id"] = recorded, updateOfferID
	r.saveSession(session)
	r.saveRules(rules)
	r.appendAudit(map[string]any{
		"action": "update_offer", "actor": "owner", "origin": r.origin, "choice": recorded, "id": updateOfferID,
	})
	out := map[string]any{"ok": true, "choice": recorded, "update_offer": r.updateOfferPublic()}
	if accepted {
		for k, v := range r.revisit() {
			out[k] = v
		}
		session, rules = r.loadSession(), r.loadRules()
		session["update_offer"], session["update_offer_id"], session["update_offer_at"] = "accepted", updateOfferID, now
		rules["update_offer"], rules["update_offer_id"] = "accepted", updateOfferID
		r.saveSession(session)
		r.saveRules(rules)
		out["update_offer"] = r.updateOfferPublic()
		out["session"] = r.publicSession()
	}
	return out
}
