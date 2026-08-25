"""Owner language — UI chrome catalogs + reply-language for the partner.

Remedy is for everyone. ``ui_language=auto`` (default) matches what the
partner writes and the OS language. An explicit code pins both chrome and
replies. Help manuals stay English until they are translated.
"""

from __future__ import annotations

from remedy.i18n.catalog import chrome_catalog
from remedy.i18n.languages import (
    LANGUAGE_ROWS,
    is_rtl,
    language_system_line,
    normalize_ui_language,
    public_language_list,
    resolve_ui_language,
)

__all__ = [
    "LANGUAGE_ROWS",
    "chrome_catalog",
    "is_rtl",
    "language_system_line",
    "normalize_ui_language",
    "public_language_list",
    "resolve_ui_language",
]
