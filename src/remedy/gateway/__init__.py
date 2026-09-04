"""Gateway package — catalog, settings, and outbound helpers.

Messenger *inbound* (Telegram, Discord, Slack Socket Mode, Matrix /sync,
Mattermost WS, WhatsApp/Teams/Google Chat webhooks, Signal receive, poll locks)
is owned by Go ``native/go/gateway`` + ``native/go/httpapi`` via
``remedy-runtime`` (Signal send/receive via Zig authorized exec-capture).
Python adapters are outbound-only stubs for TestClient / catalog compatibility.
Production cannot enable Python dual-poll; inbound implementations were removed.
"""
