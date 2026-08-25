"""Owner language: chrome catalogs, aliases, reply-language prompt."""

from __future__ import annotations

from fastapi.testclient import TestClient

from remedy.core.react_policy import build_system_prompt
from remedy.i18n.catalog import chrome_catalog
from remedy.i18n.languages import (
    LANGUAGE_ROWS,
    is_rtl,
    language_system_line,
    normalize_ui_language,
    resolve_ui_language,
)
from remedy.interfaces.api import create_app


def test_normalize_unknown_is_auto_not_english_lock():
    assert normalize_ui_language(None) == "auto"
    assert normalize_ui_language("AUTO") == "auto"
    assert normalize_ui_language("not-a-real-lang") == "auto"
    assert normalize_ui_language("ja") == "ja"
    assert normalize_ui_language("zh-CN") == "zh-Hans"
    assert normalize_ui_language("pt-BR") == "pt"
    assert normalize_ui_language("nb-NO") == "no"


def test_auto_resolves_from_os_hint():
    assert resolve_ui_language("auto", hint="es-MX") == "es"
    assert resolve_ui_language("auto", hint=None) == "en"
    assert resolve_ui_language("ja", hint="en-US") == "ja"


def test_rtl_flags():
    assert is_rtl("ar")
    assert is_rtl("he")
    assert is_rtl("fa")
    assert is_rtl("ur")
    assert not is_rtl("en")
    assert not is_rtl("ja")


def test_chrome_falls_back_to_english_for_missing_keys():
    es = chrome_catalog("es")
    assert es["bar.help"] == "Ayuda"
    assert es["menu.settings"]
    assert es["settings.title"]
    assert es["setup.getStarted"]
    assert es["plan.approve"]
    assert "{name}" in es["empty.readyNamed"]
    # Overlay may omit a key — English fills it.
    ha = chrome_catalog("ha")
    assert ha["bar.help"] == "Help"
    assert ha["composer.placeholder"]


def test_final_pass_chrome_is_translated():
    fr = chrome_catalog("fr")
    assert fr["settings.title"] == "Réglages"
    assert fr["setup.getStarted"] == "Commencer"
    assert "{name}" in fr["empty.readyNamed"]
    ar = chrome_catalog("ar")
    assert ar["settings.title"] == "الإعدادات"
    ja = chrome_catalog("ja")
    assert ja["setup.skipRemaining"]


def test_chrome_languages_cover_every_english_key():
    from remedy.i18n.catalog import EN
    from remedy.i18n.chrome_fill import FILL
    from remedy.i18n.chrome_final import FINAL
    from remedy.i18n.chrome_translations import TRANSLATIONS

    for row in LANGUAGE_ROWS:
        if not row.chrome or row.id == "en":
            continue
        have = (
            set(TRANSLATIONS.get(row.id) or {})
            | set(FILL.get(row.id) or {})
            | set(FINAL.get(row.id) or {})
        )
        missing = sorted(set(EN) - have)
        assert missing == [], f"{row.id} missing {missing[:12]}"


def test_many_languages_are_listed():
    ids = {row.id for row in LANGUAGE_ROWS}
    for need in (
        "es",
        "pt",
        "ar",
        "hi",
        "zh-Hans",
        "zh-Hant",
        "ja",
        "ko",
        "sw",
        "yo",
        "ha",
        "am",
        "ta",
        "vi",
        "id",
    ):
        assert need in ids
    assert len(LANGUAGE_ROWS) >= 60


def test_reply_language_is_in_the_system_prompt():
    auto = build_system_prompt("balanced", name="Remedy", gender="female", ui_language="auto")
    assert "reply in the language the partner is writing in" in auto.lower()
    ja = build_system_prompt("balanced", name="Remedy", gender="female", ui_language="ja")
    assert "Japanese" in ja
    # Language is operational — tools and checkpoints stay.
    assert "file_write" in ja or "Tools" in ja or "tools" in ja.lower()


def test_auto_prompt_does_not_force_english():
    line = language_system_line("auto")
    assert "English" not in line or "unless they do" in line


def test_i18n_endpoint_and_settings_round_trip(tmp_path, monkeypatch):
    monkeypatch.setenv("REMEDY_HOME", str(tmp_path))
    client = TestClient(create_app())
    hinted = client.get("/api/i18n", params={"lang": "auto", "hint": "pt-BR"})
    assert hinted.status_code == 200
    assert hinted.json()["resolved"] == "pt"

    r = client.get("/api/i18n", params={"lang": "es"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["resolved"] == "es"
    assert body["catalog"]["bar.help"] == "Ayuda"
    assert any(x["id"] == "auto" for x in body["languages"])
    assert any(x["id"] == "yo" for x in body["languages"])

    s = client.get("/api/settings")
    assert s.status_code == 200
    data = s.json()
    assert data.get("ui_language") == "auto"
    assert isinstance(data.get("ui_languages"), list)
    assert len(data["ui_languages"]) >= 60

    put = client.put("/api/settings", json={"ui_language": "ja"})
    assert put.status_code == 200, put.text
    again = client.get("/api/settings")
    assert again.json()["ui_language"] == "ja"
    i18n = client.get("/api/i18n")
    assert i18n.json()["resolved"] == "ja"
    assert i18n.json()["catalog"]["settings.save"] == "保存"
