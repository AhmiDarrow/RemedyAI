"""Gateway package — catalog, settings, and TestClient adapter stubs.

Messenger network I/O (Telegram/Discord/Slack/Matrix/Mattermost poll/WS,
WhatsApp/Teams/Google Chat webhooks, Signal via Zig exec-capture, and all
outbound REST/CLI sends) is owned by Go ``native/go/gateway`` +
``native/go/httpapi`` via ``remedy-runtime``.

Python keeps ``messengers.py`` catalog/settings schema and shape-compatible
channel stubs for TestClient — no aiohttp, no signal-cli spawn, no dual-poll.
"""
