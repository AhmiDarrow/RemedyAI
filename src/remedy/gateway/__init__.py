"""Gateway package — messenger catalog + CLI helpers only.

Messenger inbound *and* outbound (Telegram, Discord, Slack Socket Mode,
Matrix /sync, Mattermost WS, WhatsApp/Teams/Google Chat webhooks, Signal,
desktop mirror) are owned by Go ``native/go/gateway`` + ``native/go/httpapi``
via ``remedy-runtime``. Python keeps the Settings field catalog for TestClient
parity; network adapter twins were removed.
"""
