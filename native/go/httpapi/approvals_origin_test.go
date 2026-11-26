package httpapi

import "testing"

// A hive forager runs in its parent's session. The owner's "just this once"
// for a payment step used to be spendable by the forager's identical call.
func TestOneShotGrantIsSpentOnlyByItsOwnOrigin(t *testing.T) {
	q := newApprovalQueue()
	_ = q.SetMode("ask")
	sid := "sess-parent"
	cmd := `{"argv":["pay","--amount","40"]}`
	owner := q.EnqueueOrigin("shell.exec", cmd, "Tool requires your approval", &sid, "", "desktop")
	_ = q.Resolve(owner.ID, true, "once")

	if q.IsOneShotApproved("shell.exec", cmd, sid, "hive:"+sid, "hive-call") {
		t.Fatal("a hive turn spent the owner's one-shot grant")
	}
	if q.IsApproved("shell.exec", cmd, sid, "telegram:99", false) {
		t.Fatal("a messenger turn spent the owner's one-shot grant")
	}
	if !q.IsOneShotApproved("shell.exec", cmd, sid, "desktop", "owner-call") {
		t.Fatal("the owner's own retry must use its grant")
	}

	hive := q.EnqueueOrigin("shell.exec", cmd, "Tool requires your approval", &sid, "", "hive:"+sid)
	_ = q.Resolve(hive.ID, true, "once")
	if q.IsApproved("shell.exec", cmd, sid, "hive:other", false) {
		t.Fatal("another hive spent this hive's grant")
	}
	if !q.IsApproved("shell.exec", cmd, sid, "HIVE:"+sid, false) {
		t.Fatal("the hive's own retry must use its grant")
	}
}
