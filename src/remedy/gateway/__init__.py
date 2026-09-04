"""Gateway package — catalog, settings, and outbound helpers.

Messenger *inbound* (Telegram, Discord, Slack Socket Mode, Matrix /sync,
Mattermost WS, WhatsApp/Teams/Google Chat webhooks, Signal receive, poll locks)
is owned by Go ``native/go/gateway`` + ``native/go/httpapi`` via
``remedy-runtime`` (Signal send/receive via Zig authorized exec-capture).
Python adapters remain for catalog/settings and pytest inbound only
(``REMEDY_PYTHON_MESSENGER_POLL=1`` plus ``REMEDY_TESTING`` /
``PYTEST_CURRENT_TEST``). Production cannot enable Python dual-poll by flag
alone.
"""
