from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
API_PATH = REPO_ROOT / "src" / "remedy" / "interfaces" / "api.py"


def test_api_exists():
    """The API interface module must exist in the repository."""
    assert API_PATH.is_file(), f"Expected API module at {API_PATH}"


def test_api():
    """The API interface module should be a non-empty Python file."""
    if not API_PATH.exists():
        assert False, f"Missing API module: {API_PATH}"
    else:
        content = API_PATH.read_text(encoding="utf-8")
        assert content.strip(), f"API module {API_PATH} is empty"
        assert content.lstrip().startswith(("import", "from", "\"\"\"", "'''", "#")), (
            f"API module {API_PATH} does not look like a Python file"
        )
