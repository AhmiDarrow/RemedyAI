"""Gateway package — catalog, settings, and outbound helpers.

Messenger *inbound* (Telegram getUpdates, Discord gateway, poll locks) is owned
by Go ``native/go/gateway`` via ``remedy-runtime``. Python adapters remain for
outbound mirror on the compatibility serve path and for unit tests. Set
``REMEDY_PYTHON_MESSENGER_POLL=1`` only to force Python inbound (emergency).
"""
