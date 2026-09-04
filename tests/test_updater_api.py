"""Update check API shape."""

from __future__ import annotations

# GET /api/updates/check cache behaviour is covered by Go httpapi/updates_test.go.


def test_is_trusted_download_url_logic():
    """Mirror Rust allowlist intent in a pure-Python check for docs/regression."""
    def trusted(url: str) -> bool:
        if url.startswith("https://github.com/AhmiDarrow/RemedyAI/releases/"):
            return True
        if url.startswith("https://objects.githubusercontent.com/") or url.startswith(
            "https://release-assets.githubusercontent.com/"
        ):
            return ".." not in url and len(url) < 2048
        return False

    assert trusted(
        "https://github.com/AhmiDarrow/RemedyAI/releases/download/"
        "v0.41.6/Remedy.Desktop_0.41.6_x64-setup.exe"
    )
    assert not trusted("https://github.com/evil/repo/releases/download/v1/x.exe")
    assert not trusted("http://evil.com/x.exe")
