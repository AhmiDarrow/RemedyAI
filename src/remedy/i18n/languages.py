"""Language registry — BCP-47 ids, native names, RTL, prompt labels.

Chrome catalogs may lag the list. Every row is valid for reply-language:
the model is told to speak that language even if the UI is still English.
"""

from __future__ import annotations

from dataclasses import dataclass

AUTO = "auto"


@dataclass(frozen=True)
class Language:
    id: str
    name_en: str
    name_native: str
    prompt_name: str
    rtl: bool = False
    """True when a chrome catalog exists (UI strings). Reply-language always works."""
    chrome: bool = False


def _lang(
    id: str,
    name_en: str,
    name_native: str,
    prompt_name: str | None = None,
    *,
    rtl: bool = False,
    chrome: bool = False,
) -> Language:
    return Language(
        id=id,
        name_en=name_en,
        name_native=name_native,
        prompt_name=prompt_name or name_en,
        rtl=rtl,
        chrome=chrome,
    )


# Order: Auto is not in this tuple. High-coverage first, then regional.
LANGUAGE_ROWS: tuple[Language, ...] = (
    _lang("en", "English", "English", chrome=True),
    _lang("es", "Spanish", "Español", chrome=True),
    _lang("pt", "Portuguese", "Português", chrome=True),
    _lang("fr", "French", "Français", chrome=True),
    _lang("de", "German", "Deutsch", chrome=True),
    _lang("it", "Italian", "Italiano", chrome=True),
    _lang("nl", "Dutch", "Nederlands", chrome=True),
    _lang("pl", "Polish", "Polski", chrome=True),
    _lang("ru", "Russian", "Русский", chrome=True),
    _lang("uk", "Ukrainian", "Українська", chrome=True),
    _lang("tr", "Turkish", "Türkçe", chrome=True),
    _lang("ar", "Arabic", "العربية", rtl=True, chrome=True),
    _lang("hi", "Hindi", "हिन्दी", chrome=True),
    _lang("bn", "Bengali", "বাংলা", chrome=True),
    _lang("ur", "Urdu", "اردو", rtl=True, chrome=True),
    _lang("id", "Indonesian", "Bahasa Indonesia", chrome=True),
    _lang("ms", "Malay", "Bahasa Melayu", chrome=True),
    _lang("vi", "Vietnamese", "Tiếng Việt", chrome=True),
    _lang("th", "Thai", "ไทย", chrome=True),
    _lang("ja", "Japanese", "日本語", chrome=True),
    _lang("ko", "Korean", "한국어", chrome=True),
    _lang("zh-Hans", "Chinese (Simplified)", "简体中文", "Simplified Chinese", chrome=True),
    _lang("zh-Hant", "Chinese (Traditional)", "繁體中文", "Traditional Chinese", chrome=True),
    _lang("fil", "Filipino", "Filipino", chrome=True),
    _lang("sw", "Swahili", "Kiswahili", chrome=True),
    _lang("ha", "Hausa", "Hausa"),
    _lang("yo", "Yoruba", "Yorùbá"),
    _lang("ig", "Igbo", "Igbo"),
    _lang("zu", "Zulu", "isiZulu"),
    _lang("am", "Amharic", "አማርኛ"),
    _lang("he", "Hebrew", "עברית", rtl=True, chrome=True),
    _lang("fa", "Persian", "فارسی", rtl=True, chrome=True),
    _lang("ps", "Pashto", "پښتو", rtl=True),
    _lang("ku", "Kurdish", "Kurdî"),
    _lang("ta", "Tamil", "தமிழ்"),
    _lang("te", "Telugu", "తెలుగు"),
    _lang("mr", "Marathi", "मराठी"),
    _lang("gu", "Gujarati", "ગુજરાતી"),
    _lang("kn", "Kannada", "ಕನ್ನಡ"),
    _lang("ml", "Malayalam", "മലയാളം"),
    _lang("pa", "Punjabi", "ਪੰਜਾਬੀ"),
    _lang("ne", "Nepali", "नेपाली"),
    _lang("si", "Sinhala", "සිංහල"),
    _lang("my", "Burmese", "မြန်မာ"),
    _lang("km", "Khmer", "ខ្មែរ"),
    _lang("lo", "Lao", "ລາວ"),
    _lang("sv", "Swedish", "Svenska", chrome=True),
    _lang("da", "Danish", "Dansk", chrome=True),
    _lang("no", "Norwegian", "Norsk", chrome=True),
    _lang("fi", "Finnish", "Suomi", chrome=True),
    _lang("hu", "Hungarian", "Magyar", chrome=True),
    _lang("cs", "Czech", "Čeština", chrome=True),
    _lang("sk", "Slovak", "Slovenčina"),
    _lang("ro", "Romanian", "Română", chrome=True),
    _lang("bg", "Bulgarian", "Български"),
    _lang("el", "Greek", "Ελληνικά", chrome=True),
    _lang("hr", "Croatian", "Hrvatski"),
    _lang("sr", "Serbian", "Srpski"),
    _lang("sl", "Slovenian", "Slovenščina"),
    _lang("lt", "Lithuanian", "Lietuvių"),
    _lang("lv", "Latvian", "Latviešu"),
    _lang("et", "Estonian", "Eesti"),
    _lang("ca", "Catalan", "Català", chrome=True),
    _lang("eu", "Basque", "Euskara"),
    _lang("gl", "Galician", "Galego"),
    _lang("ga", "Irish", "Gaeilge"),
    _lang("cy", "Welsh", "Cymraeg"),
    _lang("is", "Icelandic", "Íslenska"),
    _lang("sq", "Albanian", "Shqip"),
    _lang("mk", "Macedonian", "Македонски"),
    _lang("bs", "Bosnian", "Bosanski"),
    _lang("be", "Belarusian", "Беларуская"),
    _lang("ka", "Georgian", "ქართული"),
    _lang("hy", "Armenian", "Հայերեն"),
    _lang("az", "Azerbaijani", "Azərbaycan"),
    _lang("kk", "Kazakh", "Қазақша"),
    _lang("uz", "Uzbek", "Oʻzbek"),
    _lang("mn", "Mongolian", "Монгол"),
    _lang("ky", "Kyrgyz", "Кыргызча"),
    _lang("tg", "Tajik", "Тоҷикӣ"),
    _lang("tk", "Turkmen", "Türkmen"),
    _lang("jv", "Javanese", "Basa Jawa"),
    _lang("su", "Sundanese", "Basa Sunda"),
    _lang("ceb", "Cebuano", "Cebuano"),
    _lang("yi", "Yiddish", "ייִדיש", rtl=True),
)

LANGUAGE_BY_ID: dict[str, Language] = {row.id.lower(): row for row in LANGUAGE_ROWS}

# OS / browser tags → our ids
_ALIASES: dict[str, str] = {
    "en-us": "en",
    "en-gb": "en",
    "en-au": "en",
    "en-ca": "en",
    "es-mx": "es",
    "es-ar": "es",
    "es-es": "es",
    "es-419": "es",
    "pt-br": "pt",
    "pt-pt": "pt",
    "zh": "zh-Hans",
    "zh-cn": "zh-Hans",
    "zh-sg": "zh-Hans",
    "zh-tw": "zh-Hant",
    "zh-hk": "zh-Hant",
    "zh-mo": "zh-Hant",
    "nb": "no",
    "nn": "no",
    "nb-no": "no",
    "fil-ph": "fil",
    "tl": "fil",
    "iw": "he",
    "in": "id",
    "jp": "ja",
    "ua": "uk",
    "fa-ir": "fa",
    "ar-sa": "ar",
    "ar-eg": "ar",
}


def normalize_ui_language(raw: str | None) -> str:
    """Stored setting: ``auto`` or a known id. Unknown → auto (never drop the owner)."""
    s = (raw or "").strip()
    if not s or s.lower() in ("auto", "default", "system", "os"):
        return AUTO
    key = s.replace("_", "-")
    low = key.lower()
    if low in LANGUAGE_BY_ID:
        return LANGUAGE_BY_ID[low].id
    if low in _ALIASES:
        return _ALIASES[low]
    prefix = low.split("-", 1)[0]
    if prefix in LANGUAGE_BY_ID:
        return LANGUAGE_BY_ID[prefix].id
    if prefix in _ALIASES:
        return _ALIASES[prefix]
    return AUTO


def resolve_ui_language(
    stored: str | None,
    *,
    hint: str | None = None,
) -> str:
    """Concrete id for chrome/prompt. ``auto`` uses *hint* (OS / Accept-Language)."""
    code = normalize_ui_language(stored)
    if code != AUTO:
        return code
    hinted = normalize_ui_language(hint)
    return hinted if hinted != AUTO else "en"


def is_rtl(code: str | None) -> bool:
    row = LANGUAGE_BY_ID.get(normalize_ui_language(code).lower())
    return bool(row and row.rtl)


def public_language_list() -> list[dict[str, object]]:
    """Payload for Settings / GET /api/i18n."""
    rows: list[dict[str, object]] = [
        {
            "id": AUTO,
            "name_en": "Auto",
            "name_native": "Auto",
            "rtl": False,
            "chrome": True,
        }
    ]
    for row in LANGUAGE_ROWS:
        rows.append(
            {
                "id": row.id,
                "name_en": row.name_en,
                "name_native": row.name_native,
                "rtl": row.rtl,
                "chrome": row.chrome,
            }
        )
    return rows


def language_system_line(code: str | None) -> str:
    """Operational prompt line — does not change identity, tools, or checkpoints."""
    stored = normalize_ui_language(code)
    if stored == AUTO:
        return (
            "Language: reply in the language the partner is writing in. "
            "Match them turn by turn. Do not switch to English unless they do. "
            "Greetings, plans, and explanations stay in their language. "
            "Code, file paths, tool names, and JSON stay as written."
        )
    row = LANGUAGE_BY_ID.get(stored.lower())
    name = row.prompt_name if row else stored
    return (
        f"Language: the partner chose {name} ({stored}). "
        f"Write your replies in {name}. If they clearly write in a different "
        "language this turn, match that language instead. "
        "Do not lecture them about language. "
        "Code, file paths, tool names, and JSON stay as written."
    )
