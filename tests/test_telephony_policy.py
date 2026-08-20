"""Call policy, transcripts, and hard checkpoints — no live trunk required."""

from __future__ import annotations

from pathlib import Path

from remedy.telephony.checkpoints import REFUSAL, may_speak
from remedy.telephony.policy import for_contact, set_contact
from remedy.telephony.transcript import CallTranscript, load


def test_default_policy_discloses(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("REMEDY_HOME", str(tmp_path))
    pol = for_contact("+15551212", tmp_path)
    assert pol.disclose is True
    assert "assistant" in pol.opening_line().lower()
    set_contact("+15551212", tmp_path, disclose=False)
    again = for_contact("+15551212", tmp_path)
    assert again.disclose is False
    assert again.opening_line()  # record notice still on


def test_transcript_roundtrip(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("REMEDY_HOME", str(tmp_path))
    tr = CallTranscript(call_id="abc123", remote="+1555")
    tr.add("Remedy", "Hello, this is Remedy.")
    tr.add("them", "Hi, I need an appointment.")
    tr.save(tmp_path)
    loaded = load("abc123", tmp_path)
    assert loaded is not None
    assert "appointment" in loaded.as_plain()
    assert loaded.turns[0].who == "Remedy"


def test_may_speak_blocks_secrets_and_agreements():
    ok, why = may_speak("The next available is Tuesday morning.")
    assert ok and why is None
    blocked, msg = may_speak("The card number is 4111 1111 1111 1111")
    assert blocked is False
    assert msg == REFUSAL
    blocked2, _ = may_speak("Yes, please charge the card on file.")
    assert blocked2 is False
    for said in (
        "My verification code is 482913.",
        "The CVV is 123.",
        "I agree to the charge of forty dollars.",
        "Go ahead and cancel the plan.",
        "The password is hunter2",
    ):
        assert may_speak(said)[0] is False, said


def test_may_speak_allows_ordinary_talk_about_codes_and_agreement():
    for said in (
        "I agree, that sounds frustrating.",
        "Did they send you a verification code?",
        "I accept that the wait was long.",
        "Which card number do you want me to ask about - the last four?",
        "She will need her account number handy; I will not read it out.",
        "Please send the security code to the owner, not to me.",
        "I authorize nothing on this call; the owner decides.",
    ):
        ok, why = may_speak(said)
        assert ok and why is None, said


def test_local_tts_refuses_a_card_number(monkeypatch):
    import asyncio

    from remedy.telephony.checkpoints import REFUSAL
    from remedy.voice.realtime.tts import LocalTts
    from remedy.voice.service import encode_wav

    seen: list[str] = []

    def fake_synth(text, gender=None, home_dir=None):
        seen.append(text)
        return encode_wav([0.0, 0.1], 24_000), 24_000

    monkeypatch.setattr("remedy.voice.service.synthesize", fake_synth)

    async def go():
        tts = LocalTts()
        return [c async for c in tts.stream("The card number is 4111111111111111")]

    asyncio.run(go())
    assert seen and seen[0] == REFUSAL


def test_tts_stream_pads_last_frame():
    from remedy.voice.realtime.tts_stream import frame_size, iter_frames

    sr = 8000
    step = frame_size(sr)
    pcm = b"\x01\x00" * 10  # 20 bytes, one frame is 320 at 8 kHz
    frames = list(iter_frames(pcm, sr))
    assert frames
    assert all(len(f) == step for f in frames)
    assert frames[0].startswith(b"\x01\x00")
