"""Gateway package — catalog, settings, and outbound helpers.

Messenger *inbound* (Telegram, Discord, Slack Socket Mode, Matrix /sync,
Mattermost WS, poll locks) is owned by Go ``native/go/gateway`` via
``remedy-runtime``. Python adapters remain for catalog/settings, unit tests,
and emergency ``REMEDY_PYTHON_MESSENGER_POLL=1`` inbound only.
"""
