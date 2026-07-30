"""Inspect chat message image URL shapes in memory.db."""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path

db = Path.home() / ".remedy" / "memory.db"
con = sqlite3.connect(str(db))
con.row_factory = sqlite3.Row
print("tables:", [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")])
for t in ("chat_sessions", "sessions", "chat_messages", "messages"):
    try:
        cols = [c[1] for c in con.execute(f"PRAGMA table_info({t})")]
        print(t, cols)
    except Exception as e:
        print(t, "n/a", e)

# discover message table
msg_table = None
for t in ("chat_messages", "messages", "session_messages"):
    try:
        con.execute(f"SELECT 1 FROM {t} LIMIT 1")
        msg_table = t
        break
    except Exception:
        pass
print("msg_table", msg_table)
if not msg_table:
    raise SystemExit(1)

# find sticky sessions
sess_table = None
for t in ("chat_sessions", "sessions"):
    try:
        con.execute(f"SELECT 1 FROM {t} LIMIT 1")
        sess_table = t
        break
    except Exception:
        pass
print("sess_table", sess_table)

if sess_table:
    rows = con.execute(
        f"SELECT * FROM {sess_table} ORDER BY rowid DESC LIMIT 20"
    ).fetchall()
    keys = rows[0].keys() if rows else []
    print("sess keys", list(keys))
    for r in rows[:10]:
        d = dict(r)
        title = str(d.get("title") or d.get("name") or "")[:40]
        proj = str(d.get("project_path") or d.get("project") or "")[:60]
        sid = str(d.get("id") or d.get("session_id") or "")[:12]
        print("S", sid, title, proj)

# sample messages with image-like content
q = f"""
SELECT * FROM {msg_table}
WHERE content LIKE '%![%'
   OR content LIKE '%data:image%'
   OR content LIKE '%.png%'
   OR content LIKE '%.jpg%'
   OR content LIKE '%attachments%'
ORDER BY rowid DESC LIMIT 25
"""
msgs = con.execute(q).fetchall()
print("img-like messages", len(msgs))
for m in msgs[:15]:
    d = dict(m)
    c = str(d.get("content") or "")
    sid = str(d.get("session_id") or d.get("session") or "")[:12]
    urls = re.findall(r"!\[[^\]]*\]\(([^)]+)\)", c)
    print("---")
    print("sid", sid, "role", d.get("role"), "n_md_imgs", len(urls))
    for u in urls[:5]:
        print(" ", u[:160].replace("\n", " "))
    if not urls:
        print(" snippet", c[:200].replace("\n", " "))
