"""Gateway package — catalog, settings, and outbound helpers.

Messenger *inbound* (Telegram, Discord, Slack Socket Mode, Matrix /sync,
Mattermost WS, WhatsApp/Teams/Google Chat webhooks, poll locks) is owned by Go
``native/go/gateway`` + ``native/go/httpapi`` via ``remedy-runtime``. Python
adapters remain for catalog/settings, unit tests, and emergency
``REMEDY_PYTHON_MESSENGER_POLL=1`` inbound only. Signal stays external
(signal-cli / Zig).
"""
