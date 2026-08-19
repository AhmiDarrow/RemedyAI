"""What a session gets called in the sidebar.

The whole reason this module exists: people paste a file path or drop a
screenshot as their first message, and the session ends up titled
``C:\\Users\\Administrator\\Pictures\\Screenshot 2026-08-19 143022.png``. A
sidebar of those is a sidebar you cannot read. So a path becomes the thing at
the end of it, a screenshot loses its timestamp, and everything is bounded.

Pure functions, no I/O.
"""

from __future__ import annotations

import pytest

from remedy.interfaces.routes.sessions.titles import (
    looks_like_path_title,
    title_from_attachment_name,
    title_from_prompt,
)

# --- recognising a path ------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        r"C:\Users\me\Pictures\shot.png",
        r"D:/projects/remedy/notes.md",
        r"\\fileserver\share\report.pdf",
        "/Users/me/Desktop/thing.png",
        "/home/me/downloads/scan.pdf",
        r"some\folder\invoice.pdf",
        r"deep\path\photo.JPEG",
        "Screenshot 2026-08-19 143022.png",
    ],
)
def test_these_are_paths_not_titles(text):
    assert looks_like_path_title(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        "help me plan the week",
        "what is 2 + 2",
        "Screenshot the login page for me",  # an instruction, not a filename
        "notes.md",  # a bare filename is a fine title
        "screenshot_final.webp",  # ditto — no separator, no timestamp
        "C: is nearly full",  # drive letter, but no separator
        "read /etc/hosts and tell me what you see",  # a sentence containing a path
    ],
)
def test_these_are_ordinary_messages(text):
    assert looks_like_path_title(text) is False


# --- turning a filename into a name ------------------------------------------


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        (r"C:\Users\me\Pictures\holiday_photo.png", "holiday photo"),
        ("/home/me/quarterly-report.pdf", "quarterly report.pdf"),
        ("my_notes.md", "my notes.md"),
        ("invoice-2026.jpeg", "invoice 2026"),
        ("multi__under___scores.png", "multi under scores"),
        ("  spaced   out.webp  ", "spaced out"),
    ],
)
def test_a_filename_becomes_something_readable(name, expected):
    assert title_from_attachment_name(name) == expected


def test_an_image_extension_is_dropped():
    assert title_from_attachment_name("sunset.HEIC") == "sunset"


def test_a_document_extension_is_kept():
    """A .pdf in the title tells the owner what it is; an image is obvious."""
    assert title_from_attachment_name("contract.pdf") == "contract.pdf"


def test_a_screenshot_loses_its_timestamp():
    """Otherwise every screenshot session looks identical in the sidebar."""
    assert title_from_attachment_name("Screenshot 2026-08-19 143022.png") == "Screenshot"


def test_a_screenshot_with_no_timestamp_survives():
    assert title_from_attachment_name("Screenshot.png") == "Screenshot"


def test_nothing_at_all_still_gets_a_name():
    assert title_from_attachment_name("") == "Attachment"
    assert title_from_attachment_name("   ") == "Attachment"


def test_a_filename_that_is_only_an_extension_still_gets_a_name():
    assert title_from_attachment_name(".png") == "Image"


def test_a_long_filename_is_bounded_and_marked():
    out = title_from_attachment_name("a_very_" + "long_" * 40 + "name.png")
    assert len(out) <= 52
    assert out.endswith("…")


def test_the_bound_is_adjustable():
    assert len(title_from_attachment_name("a_" * 60 + "name.png", max_len=20)) <= 20


# --- titling from the first message ------------------------------------------


def test_an_ordinary_message_is_its_own_title():
    assert title_from_prompt("plan my week around the dentist") == (
        "plan my week around the dentist"
    )


def test_whitespace_is_collapsed():
    assert title_from_prompt("plan   my\n\nweek") == "plan my week"


def test_an_empty_message_gets_a_placeholder():
    assert title_from_prompt("") == "New Session"
    assert title_from_prompt("    ") == "New Session"


def test_a_long_message_is_bounded_and_marked():
    out = title_from_prompt("think about " * 40)
    assert len(out) <= 52
    assert out.endswith("…")


def test_a_pasted_path_becomes_the_file_it_points_at():
    """The bug this module exists for."""
    assert title_from_prompt(r"C:\Users\me\Pictures\holiday_photo.png") == "holiday photo"


def test_a_pasted_screenshot_path_becomes_just_screenshot():
    assert title_from_prompt(
        r"C:\Users\me\Pictures\Screenshot 2026-08-19 143022.png"
    ) == "Screenshot"


def test_the_attachment_block_is_cut_from_the_title():
    """The 📎 block is UI furniture, not something the owner typed."""
    assert title_from_prompt("look at this 📎 shot.png (1.2 MB)") == "look at this"


def test_a_message_that_is_only_an_attachment_block_keeps_something():
    assert title_from_prompt("📎 shot.png") != ""


def test_an_attachment_only_message_is_named_after_the_attachment():
    out = title_from_prompt(
        "(see attached)", att_dicts=[{"name": "quarterly_report.png"}]
    )
    assert out == "quarterly report"


def test_an_attachment_only_message_with_no_attachment_still_names_something():
    assert title_from_prompt("(see attached)") == "Attachments"


def test_an_attachment_only_message_is_matched_case_insensitively():
    out = title_from_prompt("(See Attached)", att_dicts=[{"name": "scan.png"}])
    assert out == "scan"


def test_a_sentence_merely_mentioning_an_attachment_is_left_alone():
    """It only counts when the message *is* the attachment note."""
    text = "the numbers in the file I see attached do not add up"
    assert title_from_prompt(text) == text


def test_the_bound_applies_after_a_path_is_prettified():
    out = title_from_prompt("C:\\x\\" + "long_" * 40 + "name.png")
    assert len(out) <= 52
