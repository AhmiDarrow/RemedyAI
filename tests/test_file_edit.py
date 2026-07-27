"""file_edit apply_search_replace + multi-hunk."""

from remedy.core.file_edit import apply_multi_hunk, apply_search_replace


def test_unique_replace():
    r = apply_search_replace("hello world", "world", "there")
    assert r.ok
    assert r.new_content == "hello there"
    assert r.occurrences == 1


def test_not_found():
    r = apply_search_replace("abc", "zzz", "q")
    assert not r.ok
    assert "not found" in r.message.lower()


def test_multiple_requires_replace_all():
    r = apply_search_replace("aa aa", "aa", "b")
    assert not r.ok
    assert "2 times" in r.message
    r2 = apply_search_replace("aa aa", "aa", "b", replace_all=True)
    assert r2.ok
    assert r2.new_content == "b b"


def test_multi_hunk_ok():
    src = "alpha\nbeta\ngamma\n"
    r = apply_multi_hunk(
        src,
        [
            {"old_string": "alpha", "new_string": "ALPHA"},
            {"old_string": "gamma", "new_string": "GAMMA"},
        ],
    )
    assert r.ok
    assert r.hunks_applied == 2
    assert r.new_content == "ALPHA\nbeta\nGAMMA\n"


def test_multi_hunk_stops_on_failure():
    r = apply_multi_hunk(
        "only once\n",
        [
            {"old_string": "only once", "new_string": "twice"},
            {"old_string": "missing", "new_string": "x"},
        ],
    )
    assert not r.ok
    assert r.hunks_applied == 1
    assert "hunk 1 failed" in r.message
