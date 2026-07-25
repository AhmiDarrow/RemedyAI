"""file_edit apply_search_replace."""

from remedy.core.file_edit import apply_search_replace


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
