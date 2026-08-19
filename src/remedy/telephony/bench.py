"""Phase 0 bench — prove the voice before any hardware is bought.

Runs a scripted call over the simulated circuit and reports against the human
bar in ``docs/TELEPHONY.md``. Engines are injected with declared latencies, so
the harness answers a specific question: *given an STT that takes X, a model
that takes Y, and a synthesizer that takes Z, does the conversation still sound
human?* Swap the stubs for Kokoro / whisper / Chatterbox and the same harness
measures the real thing.

    python -m remedy.telephony.bench

What it does not measure: recognition accuracy. The bench STT reads ground
truth from the script, because what fails the human bar is timing, and mixing
the two would hide which one broke.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from remedy.telephony.backends.fake import Cue, FakeBackend, FakeCall, Utterance, voiced_pcm
from remedy.telephony.narrowband import PHONE_RATE
from remedy.telephony.timing import precise_timing
from remedy.voice.realtime.metrics import BAR, CallMetrics, HumanBar
from remedy.voice.realtime.pipeline import PipelineConfig, VoicePipeline
from remedy.voice.realtime.turn import EnergyTurnDetector

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class EngineLatency:
    """Declared cost of each stage. Defaults are the local stack on a 3080.

    These are the numbers to argue with — if the bench passes here and the real
    engines are slower, the bench was lying about the engines, not the design.
    """

    #: Finalizing a short utterance (distil-whisper, GPU).
    stt_final_ms: float = 160.0
    #: Model time to first token.
    llm_ttft_ms: float = 320.0
    #: Per-token streaming interval.
    llm_token_ms: float = 12.0
    #: Synthesis time to first audio chunk.
    tts_ttfb_ms: float = 140.0
    #: Synthesis time per subsequent chunk.
    tts_chunk_ms: float = 40.0


class OracleStt:
    """Timing-accurate STT: costs what STT costs, returns what was really said.

    Deliberately stateless. Speculative turns are started and thrown away, so a
    stub that advanced an internal cursor per call would desynchronise after the
    first discarded speculation and quietly corrupt every later measurement.
    """

    def __init__(self, call: FakeCall, latency: EngineLatency) -> None:
        self._call = call
        self._latency = latency

    def feed(self, pcm: bytes, at: float) -> None:
        return None

    async def final(self) -> str:
        await asyncio.sleep(self._latency.stt_final_ms / 1000.0)
        spans = [s for s in self._call.spans if s.end]
        return spans[-1].text if spans else ""

    def reset(self) -> None:
        return None


class ScriptedResponder:
    """A model with declared time-to-first-token, streaming word by word.

    Indexed by how many far-end utterances have actually completed, not by a
    call counter — see ``OracleStt`` for why speculation forbids the latter.
    """

    def __init__(self, replies: list[str], latency: EngineLatency, call: FakeCall) -> None:
        self._replies = list(replies)
        self._latency = latency
        self._call = call

    async def reply(self, heard: str) -> AsyncIterator[str]:
        n = max(0, len([s for s in self._call.spans if s.end]) - 1)
        text = (
            self._replies[n]
            if n < len(self._replies)
            else "Sorry, could you say that again?"
        )
        await asyncio.sleep(self._latency.llm_ttft_ms / 1000.0)
        for word in text.split(" "):
            yield word + " "
            await asyncio.sleep(self._latency.llm_token_ms / 1000.0)


class ToneTts:
    """Synthesis with declared latency, emitting speech-shaped PCM."""

    def __init__(self, latency: EngineLatency, sample_rate: int = PHONE_RATE) -> None:
        self._latency = latency
        self.sample_rate = sample_rate

    async def stream(self, text: str) -> AsyncIterator[bytes]:
        words = max(1, len(text.split()))
        total_ms = words * 300
        chunk_ms = 300
        # Time-to-first-byte is paid per synthesis request, not once per call.
        # One instance serves every turn, so holding this on ``self`` would
        # charge the 140 ms warm-up to the first clause of the call and hand
        # every later turn a 40 ms head start — flattering the exact
        # time-to-first-audio numbers Phase 0 is gated on.
        first = True
        for i in range(0, total_ms, chunk_ms):
            cost = self._latency.tts_ttfb_ms if first else self._latency.tts_chunk_ms
            first = False
            await asyncio.sleep(cost / 1000.0)
            yield voiced_pcm(min(chunk_ms, total_ms - i), self.sample_rate, f0=196.0, seed=3)


@dataclass(slots=True)
class Scenario:
    """A call worth passing before trusting her with a real one."""

    name: str
    script: list[Utterance]
    replies: list[str]
    description: str = ""


def clinic_booking() -> Scenario:
    """The report's motivating example, including the interruption that breaks
    naive agents: the receptionist cuts in while she is still talking."""
    return Scenario(
        name="clinic-booking",
        description="Book an appointment with a receptionist who interrupts.",
        script=[
            Utterance("Northside Clinic, this is Dana speaking.", cue=Cue.AT_TIME, offset_ms=600),
            # Hesitates mid-sentence: a short endpointer hangover answers here,
            # over the top of her, which is exactly the failure to catch.
            Utterance(
                "Sure, can I take the patient's date of birth?",
                hesitate_at_ms=520,
                hesitate_ms=620,
            ),
            Utterance("Let me check what we have.", tail_ms=900, hesitate_at_ms=380, hesitate_ms=480),
            # Cuts across her mid-sentence 500 ms in — the barge-in case.
            Utterance("Sorry, Tuesday's gone. Thursday at ten?", cue=Cue.INTERRUPT, offset_ms=500),
            Utterance("Booked. Anything else?"),
        ],
        replies=[
            "Hi Dana, I'm calling on behalf of Ahmi Darrow to book a follow-up appointment.",
            "Of course, it's the fourth of March, nineteen ninety.",
            "Thanks. We'd prefer Tuesday morning if there's anything before noon.",
            "Thursday at ten works, let's take it.",
            "That's everything, thanks very much for your help.",
        ],
    )


def ivr_menu() -> Scenario:
    """An IVR tree: long prompts, no turn-taking cues, unforgiving timing."""
    return Scenario(
        name="ivr-menu",
        description="Automated menu with long prompts and fixed timing.",
        script=[
            Utterance(
                "Thank you for calling. Please listen carefully as our options have changed.",
                cue=Cue.AT_TIME,
                offset_ms=400,
            ),
            Utterance(
                "For appointments, say appointments. For billing, say billing.",
                tail_ms=200,
                hesitate_at_ms=700,
                hesitate_ms=560,
            ),
            Utterance("I heard appointments. Is that right?"),
            Utterance("Connecting you now."),
        ],
        replies=["Appointments.", "Appointments.", "Yes.", "Thank you."],
    )


SCENARIOS = {s.name: s for s in (clinic_booking(), ivr_menu())}


@dataclass
class BenchResult:
    scenario: str
    metrics: CallMetrics
    late_frames: int
    worst_late_ms: float
    precise_timing: bool
    #: The call never reached its own end. Whatever the numbers say, the
    #: scenario did not happen.
    timed_out: bool = False
    #: Utterances the far end never got to say, because it was still waiting.
    unsaid: int = 0
    #: How many the script had in total, so the message can say "3 of 4".
    scripted: int = 0

    @property
    def passed(self) -> bool:
        return self.metrics.passed and not self.failures

    @property
    def failures(self) -> list[str]:
        out = list(self.metrics.failures())
        if self.timed_out:
            # A hung call used to be logged and then scored on whatever it had
            # managed before it stopped — so a four-line script that stalled
            # after one exchange reported a clean pass.
            out.append(
                f"the call never finished: {self.unsaid} of {self.scripted} "
                f"scripted lines never got said"
            )
        if self.worst_late_ms > 40.0:
            out.append(
                f"playout ran late by up to {self.worst_late_ms:.0f} ms "
                f"({self.late_frames} frames) — the far end hears stutter"
            )
        return out

    def report(self) -> dict[str, Any]:
        return {
            "scenario": self.scenario,
            "passed": self.passed,
            "precise_timing": self.precise_timing,
            "timed_out": self.timed_out,
            "late_frames": self.late_frames,
            "worst_late_ms": round(self.worst_late_ms, 1),
            **self.metrics.summary(),
            "failures": self.failures,
        }


async def run_scenario(
    scenario: Scenario,
    *,
    latency: EngineLatency | None = None,
    bar: HumanBar = BAR,
    hangover_ms: float = 700.0,
    speculate_after_ms: float = 240.0,
    backchannel_after_ms: float = 400.0,
    timeout_s: float = 60.0,
    semantic: bool = False,
) -> BenchResult:
    latency = latency or EngineLatency()
    # Energy endpointing is the floor and the default, so the published numbers
    # stay comparable run to run. ``semantic=True`` swaps in the smart-turn
    # model when the owner has fetched one — the piece that is supposed to know
    # a trailing-off sentence from a finished one — and falls back on its own if
    # none is here.
    detector: Any = EnergyTurnDetector(hangover_ms=hangover_ms)
    if semantic:
        from remedy.voice.realtime.turn import make_detector

        detector = make_detector()
        if isinstance(detector, EnergyTurnDetector):
            detector.hangover_ms = hangover_ms
            logger.info("bench: no smart-turn model found; energy endpointing")
        else:
            detector.energy.hangover_ms = hangover_ms
    with precise_timing() as precise:
        backend = FakeBackend(script=scenario.script)
        call = await backend.place("+15550123")
        pipeline = VoicePipeline(
            call=call,
            stt=OracleStt(call, latency),
            responder=ScriptedResponder(scenario.replies, latency, call),
            tts=ToneTts(latency),
            detector=detector,
            metrics=CallMetrics(bar=bar),
            config=PipelineConfig(
                speculate_after_ms=speculate_after_ms,
                backchannel_after_ms=backchannel_after_ms,
            ),
            filler_audio=lambda: voiced_pcm(260, PHONE_RATE, f0=176.0, seed=9),
            far_end_speaking=lambda: call.far_end_speaking,
        )
        timed_out = False
        try:
            metrics = await asyncio.wait_for(pipeline.run(), timeout=timeout_s)
        except TimeoutError:
            logger.warning("scenario %s timed out", scenario.name)
            metrics = pipeline.metrics
            timed_out = True
        finally:
            await pipeline.stop()
    return BenchResult(
        scenario=scenario.name,
        metrics=metrics,
        late_frames=pipeline.pacer.late_frames,
        worst_late_ms=pipeline.pacer.worst_late_ms,
        precise_timing=precise,
        timed_out=timed_out,
        unsaid=max(0, len(scenario.script) - len(call.spans)),
        scripted=len(scenario.script),
    )


async def run_all(latency: EngineLatency | None = None) -> list[BenchResult]:
    return [await run_scenario(s, latency=latency) for s in SCENARIOS.values()]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Phase 0 voice bench")
    ap.add_argument("--scenario", default="", help=f"one of: {', '.join(SCENARIOS)}")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--stt-ms", type=float, default=None)
    ap.add_argument("--llm-ms", type=float, default=None)
    ap.add_argument("--tts-ms", type=float, default=None)
    ap.add_argument(
        "--semantic",
        action="store_true",
        help="use the smart-turn model when one has been fetched (else energy)",
    )
    args = ap.parse_args(argv)

    base = EngineLatency()
    latency = EngineLatency(
        stt_final_ms=args.stt_ms if args.stt_ms is not None else base.stt_final_ms,
        llm_ttft_ms=args.llm_ms if args.llm_ms is not None else base.llm_ttft_ms,
        llm_token_ms=base.llm_token_ms,
        tts_ttfb_ms=args.tts_ms if args.tts_ms is not None else base.tts_ttfb_ms,
        tts_chunk_ms=base.tts_chunk_ms,
    )

    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    chosen = [SCENARIOS[args.scenario]] if args.scenario else list(SCENARIOS.values())
    results = [
        asyncio.run(run_scenario(s, latency=latency, semantic=args.semantic))
        for s in chosen
    ]

    if args.json:
        print(json.dumps([r.report() for r in results], indent=2))
    else:
        for r in results:
            m = r.metrics.summary()
            mark = "PASS" if r.passed else "FAIL"
            print(f"\n[{mark}] {r.scenario}")
            print(
                f"  answers    p50 {m['ttfa_p50_ms']:>6.0f} ms   "
                f"p95 {m['ttfa_p95_ms']:>6.0f} ms   over {m['turns']} turns"
            )
            print(
                f"  barge-in   p95 {m['barge_in_p95_ms']:>6.0f} ms   "
                f"({m['barge_ins']} interruptions)"
            )
            print(
                f"  talks over {m['false_interrupt_rate']:.1%}   "
                f"long gaps {m['false_wait_rate']:.1%}   "
                f"worst dead air {m['worst_dead_air_ms']:.0f} ms"
            )
            print(
                f"  fillers    {m['fillers_used']}   "
                f"late frames {r.late_frames} (worst {r.worst_late_ms:.0f} ms)   "
                f"precise timing {'on' if r.precise_timing else 'OFF'}"
            )
            for line in r.failures:
                print(f"  - {line}")
    return 0 if all(r.passed for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
