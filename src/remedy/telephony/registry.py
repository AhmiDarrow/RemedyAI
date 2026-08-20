"""What this machine can actually do about phone calls, in plain sentences.

There is no setup wizard and there will not be one. When the owner says "I want
you to be able to make calls", Remedy runs these probes and *offers the choices*
(``telephony.options``), with the honest trade of each, and the owner picks.
Everything here is written to be quoted aloud: no error codes, no tracebacks,
no "ERR_NO_RADIO".

Probes must be fast and must never raise. A probe that hangs is worse than a
probe that returns "I could not tell".
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from remedy.telephony.line import Capabilities
from remedy.telephony.options import LineOption, offer

logger = logging.getLogger(__name__)

#: Nothing here waits on the owner. Probes are run while they are talking.
_PROBE_TIMEOUT_S = 4.0


@dataclass(frozen=True, slots=True)
class BackendProbe:
    """One transport's readiness, phrased for conversation."""

    name: str
    capabilities: Capabilities
    #: What is stopping this backend, in sentences.
    missing: tuple[str, ...] = ()
    #: The single next action, if a human has to take one.
    action: str = ""
    #: Extra detail worth mentioning only if asked.
    detail: str = ""

    @property
    def ready(self) -> bool:
        return not self.missing


# ---------------------------------------------------------------------------
# Bluetooth (Phase 1 audio path)
# ---------------------------------------------------------------------------


def has_bluetooth_radio() -> bool | None:
    """True / False, or None when we genuinely cannot tell.

    Uses BluetoothFindFirstRadio rather than shelling out to PowerShell, which
    takes seconds and would stall the conversation.
    """
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class _FindParams(ctypes.Structure):
            _fields_ = [("dwSize", wintypes.DWORD)]

        bthprops = ctypes.WinDLL("bthprops.cpl")
        kernel32 = ctypes.WinDLL("kernel32")
        # Without an explicit restype ctypes truncates the returned HANDLE to a
        # 32-bit signed int, and the close below would be handed a bad handle.
        # And without argtypes on the CLOSE calls, a 64-bit handle handed back
        # as a C int raises ArgumentError — caught below as "I cannot tell" on
        # exactly the machines that do have a radio.
        bthprops.BluetoothFindFirstRadio.argtypes = [
            ctypes.POINTER(_FindParams),
            ctypes.POINTER(wintypes.HANDLE),
        ]
        bthprops.BluetoothFindFirstRadio.restype = wintypes.HANDLE
        bthprops.BluetoothFindRadioClose.argtypes = [wintypes.HANDLE]
        bthprops.BluetoothFindRadioClose.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        params = _FindParams(ctypes.sizeof(_FindParams))
        radio = wintypes.HANDLE()
        finder = bthprops.BluetoothFindFirstRadio(
            ctypes.byref(params), ctypes.byref(radio)
        )
        if not finder:
            return False
        # The radio handle is an ordinary kernel object — bthprops.cpl exports
        # no BluetoothCloseHandle, and asking it for one raises AttributeError,
        # which the catch below would turn into "I cannot tell" on exactly the
        # machines that *do* have a radio.
        kernel32.CloseHandle(radio)
        bthprops.BluetoothFindRadioClose(finder)
        return True
    except Exception as exc:  # noqa: BLE001 — a probe never raises at the owner
        logger.debug("bluetooth probe failed: %s", exc)
        return None


def probe_bluetooth_hfp() -> BackendProbe:
    caps = Capabilities(outbound=True, inbound=True, dtmf_send=True, full_duplex=True)
    if sys.platform != "win32":
        return BackendProbe(
            name="bluetooth_hfp",
            capabilities=Capabilities(),
            missing=("the phone bridge needs Windows for now",),
            detail="Linux hands-free support goes through BlueZ and is not built yet.",
        )
    radio = has_bluetooth_radio()
    if radio is False:
        return BackendProbe(
            name="bluetooth_hfp",
            capabilities=Capabilities(),
            missing=("this PC has no Bluetooth radio",),
            action=(
                "A USB Bluetooth adapter would do it, but a cable does the same "
                "job more reliably and I would not start here."
            ),
            detail=(
                "Bluetooth is the only option with a distance limit, so it is "
                "never the one I suggest."
            ),
        )
    if radio is None:
        return BackendProbe(
            name="bluetooth_hfp",
            capabilities=caps,
            missing=("I could not tell whether this PC has Bluetooth",),
            action="Worth checking Settings - Bluetooth & devices before we rely on it.",
        )
    return BackendProbe(name="bluetooth_hfp", capabilities=caps)


# ---------------------------------------------------------------------------
# Android over ADB (call control, SMS, apps)
# ---------------------------------------------------------------------------


def _adb_path() -> str:
    return shutil.which("adb") or ""


def adb_devices() -> tuple[list[str], list[str]]:
    """(authorized serials, unauthorized serials). Never raises."""
    exe = _adb_path()
    if not exe:
        return [], []
    try:
        from remedy.execution.process import run_hidden

        # Status is polled from Settings: this probe must never flash a
        # console (adb also starts its server as a child — keep that quiet).
        out = run_hidden(
            [exe, "devices"],
            capture_output=True,
            text=True,
            timeout=_PROBE_TIMEOUT_S,
            check=False,
        ).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug("adb probe failed: %s", exc)
        return [], []
    ok: list[str] = []
    pending: list[str] = []
    for line in out.splitlines()[1:]:
        parts = line.split()
        if len(parts) < 2:
            continue
        if parts[1] == "device":
            ok.append(parts[0])
        elif parts[1] in ("unauthorized", "offline"):
            pending.append(parts[0])
    return ok, pending


def _is_vm(serial: str) -> bool:
    """Emulators and local VMs announce themselves; real phones do not."""
    return serial.startswith("emulator-") or serial.startswith("127.0.0.1:")


#: (authorized, pending) serials, as ``adb_devices`` returns them. Threaded
#: through the probes that need it so ``probe_all`` shells out once rather than
#: once per probe — it runs mid-conversation and adb costs up to four seconds.
AdbState = tuple[list[str], list[str]]


def probe_android(devices: AdbState | None = None) -> BackendProbe:
    caps = Capabilities(
        outbound=True, inbound=True, dtmf_send=True, sms=True, full_duplex=True
    )
    if not _adb_path():
        return BackendProbe(
            name="android",
            capabilities=Capabilities(),
            missing=("the Android platform tools are not installed",),
            action="I can install them for you — they are a small free download from Google.",
        )
    ok, pending = adb_devices() if devices is None else devices
    ok = [serial for serial in ok if not _is_vm(serial)]
    if ok:
        return BackendProbe(name="android", capabilities=caps, detail=f"connected: {ok[0]}")
    if pending:
        return BackendProbe(
            name="android",
            capabilities=caps,
            missing=("your phone is connected but has not trusted this PC yet",),
            action=(
                "There should be a prompt on the phone asking to allow USB debugging — "
                "tick 'always allow' and accept it."
            ),
        )
    return BackendProbe(
        name="android",
        capabilities=caps,
        missing=("no phone is connected with USB debugging turned on",),
        action=(
            "On the phone: Settings - About phone - tap Build number seven times, "
            "then Settings - System - Developer options - USB debugging. "
            "Plug it in and I will check again."
        ),
        detail=(
            "This one has to be you — Android deliberately does not let software "
            "turn on debugging by itself. Once it is on I can reach the phone over "
            "Wi-Fi too, so it does not have to stay near the PC."
        ),
    )


# ---------------------------------------------------------------------------
# SIP (Phase 3, her own number)
# ---------------------------------------------------------------------------


def probe_sip(home: Path | str | None = None) -> BackendProbe:
    caps = Capabilities(
        outbound=True, inbound=True, dtmf_send=True, dtmf_receive=True, full_duplex=True
    )
    exe = shutil.which("baresip")
    if not exe:
        # Fetched components land in REMEDY_HOME/bin, which is not on PATH — and
        # on Windows the file is baresip.exe, so looking only for the bare name
        # meant she could never find an engine she had downloaded herself.
        base = Path(
            home or os.environ.get("REMEDY_HOME", "~/.remedy")
        ).expanduser()
        for candidate in (base / "bin" / "baresip.exe", base / "bin" / "baresip"):
            if candidate.exists():
                exe = str(candidate)
                break
    if not exe:
        return BackendProbe(
            name="sip",
            capabilities=Capabilities(),
            missing=("the SIP engine is not installed",),
            action="I can fetch it when you want me to have my own number.",
            detail="Phase 3 — needed only for calls that do not go through your phone.",
        )
    return BackendProbe(
        name="sip",
        capabilities=caps,
        missing=("no SIP account is configured",),
        action="You would need a trunk provider account — a number costs a dollar or two a month.",
    )


# ---------------------------------------------------------------------------
# The bench always works
# ---------------------------------------------------------------------------


def probe_bench() -> BackendProbe:
    return BackendProbe(
        name="bench",
        capabilities=Capabilities(
            outbound=True, inbound=True, dtmf_send=True, dtmf_receive=True
        ),
        detail="Simulated calls, for testing my voice without dialling anyone.",
    )


@dataclass(slots=True)
class TelephonyStatus:
    probes: list[BackendProbe] = field(default_factory=list)

    def get(self, name: str) -> BackendProbe | None:
        return next((p for p in self.probes if p.name == name), None)

    @property
    def ready(self) -> list[BackendProbe]:
        return [p for p in self.probes if p.ready and p.name != "bench"]

    #: Lines that stand on their own — no second device, no second half.
    STANDALONE = ("sip", "android_vm", "phone_wired")

    @property
    def can_call(self) -> bool:
        """Any one complete path is enough.

        The phone bridge is the only one that needs two halves — ADB to dial,
        Bluetooth to carry the voice. The rest carry both themselves, and
        leaving them out of this made her deny a line she actually had.
        """
        android = self.get("android")
        bluetooth = self.get("bluetooth_hfp")
        phone_bridge = bool(android and android.ready and bluetooth and bluetooth.ready)
        if phone_bridge:
            return True
        return any(
            (probe := self.get(name)) is not None and probe.ready
            for name in self.STANDALONE
        )

    def say(self) -> str:
        """One short spoken-style status: what is missing, and what to do.

        Deliberately not a table or a checklist — this is read out in the middle
        of a conversation, so it has to survive being said aloud. Two rules:
        name only the gaps on the nearest working path (nobody wants to hear
        about the SIP engine when the real blocker is a $10 dongle), and count
        correctly, because "two things" followed by three things is the kind of
        small wrongness that makes everything else sound unreliable.
        """
        if self.can_call:
            return "I can make and take calls."

        blockers: list[str] = []
        for name in ("android", "bluetooth_hfp"):
            probe = self.get(name)
            if probe is None or probe.ready:
                continue
            for gap in probe.missing:
                blockers.append(f"{gap} — {probe.action}" if probe.action else gap)

        if not blockers:
            for name in self.STANDALONE:
                probe = self.get(name)
                if probe is not None and probe.missing:
                    return f"Not yet: {probe.missing[0]}" + (
                        f" — {probe.action}" if probe.action else ""
                    )
            return "I could not work out what is missing; worth a closer look."

        if len(blockers) == 1:
            return f"Almost — {blockers[0]}"
        counts = {2: "two", 3: "three", 4: "four"}
        count = counts.get(len(blockers), str(len(blockers)))
        return f"Not yet, {count} things. " + " ".join(
            f"{i}) {b}" for i, b in enumerate(blockers, 1)
        )


def probe_all(home: Path | str | None = None) -> TelephonyStatus:
    """Everything, fast, never raising. Safe to call mid-conversation.

    Every line ``line_options()`` offers has to be probed here too, or the two
    surfaces disagree: the menu says the Android VM is ready and the status says
    she cannot make calls.
    """
    # One adb call, shared. Three probes need it and each shell-out can cost up
    # to _PROBE_TIMEOUT_S; this is read aloud mid-sentence.
    devices: AdbState = adb_devices() if _adb_path() else ([], [])
    android = probe_android(devices)
    return TelephonyStatus(
        probes=[
            android,
            probe_bluetooth_hfp(),
            probe_android_vm(devices),
            probe_phone_wired(android),
            probe_sip(home),
            probe_bench(),
        ]
    )


# ---------------------------------------------------------------------------
# Android VM on this host (VoIP calling, and later the apps we cannot reach)
# ---------------------------------------------------------------------------


def probe_android_vm(devices: AdbState | None = None) -> BackendProbe:
    """An Android VM running here — no phone, no radio, no distance.

    Worth being exact about what this can and cannot do: a VM has no baseband
    and no SIM, and carrier voice is authenticated against the secret held
    inside the physical SIM (ISIM/USIM). So a VM can never place a *cellular*
    call on the owner's number. What it can do is run a VoIP app whose number
    lives in the cloud, with audio on this machine's own sound devices — which
    means no proximity, no pairing, and no phone battery to run flat.
    """
    caps = Capabilities(outbound=True, inbound=True, dtmf_send=True, full_duplex=True)
    if not _adb_path():
        return BackendProbe(
            name="android_vm",
            capabilities=Capabilities(),
            missing=("the Android platform tools are not installed",),
            action="I can install them — a small free download, and no phone is involved.",
        )
    ok, _ = adb_devices() if devices is None else devices
    if [s for s in ok if _is_vm(s)]:
        return BackendProbe(
            name="android_vm",
            capabilities=caps,
            # A running VM is not a phone line, the same way an installed SIP
            # engine is not a number. Reporting it ready made ``can_call`` claim
            # a line that cannot dial anyone.
            missing=("no calling app is signed in on the Android VM yet",),
            action=(
                "Sign in to a VoIP app such as Google Voice in the VM and I can "
                "call through it."
            ),
            detail="The VM is running here, so distance and phone battery do not matter.",
        )
    return BackendProbe(
        name="android_vm",
        capabilities=caps,
        missing=("there is no Android VM running on this PC yet",),
        action="I can set one up — it runs here, so distance and phone battery stop mattering.",
        detail=(
            "A VM cannot place carrier calls on your SIM number; it calls over "
            "the internet through an app such as Google Voice."
        ),
    )


# ---------------------------------------------------------------------------
# The real phone, wired for audio
# ---------------------------------------------------------------------------


def probe_phone_wired(android: BackendProbe | None = None) -> BackendProbe:
    """The owner's actual SIM, with a cable doing what Bluetooth did.

    The phone routes call audio to what it believes is a wired headset, which is
    ordinary OS behaviour rather than a workaround, and control goes over the
    network — so the phone does not have to sit next to the PC, only stay
    plugged into it.
    """
    android = probe_android() if android is None else android
    caps = Capabilities(
        outbound=True, inbound=True, dtmf_send=True, sms=True, full_duplex=True
    )
    if android.missing:
        return BackendProbe(
            name="phone_wired",
            capabilities=caps,
            missing=android.missing,
            action=android.action,
            detail=android.detail,
        )
    return BackendProbe(
        name="phone_wired",
        capabilities=caps,
        missing=("the audio cable between your phone and this PC is not set up yet",),
        action=(
            "A USB-C headphone adapter on the phone and a cable into this PC's "
            "microphone socket — about ten dollars, and then it never drops."
        ),
    )


# ---------------------------------------------------------------------------
# The offer
# ---------------------------------------------------------------------------


def line_options(home: Path | str | None = None) -> list[LineOption]:
    """Every way to get a line, with its honest trade, ready to be read aloud."""
    sip = probe_sip(home)
    devices: AdbState = adb_devices() if _adb_path() else ([], [])
    vm = probe_android_vm(devices)
    wired = probe_phone_wired(probe_android(devices))
    bt = probe_bluetooth_hfp()
    # A missing SIP engine or VM is a download; a missing Bluetooth radio is
    # hardware, and given it is also the least reliable option we do not put it
    # on the menu just to suggest buying a dongle for it.
    bt_possible = has_bluetooth_radio() is not False
    return [
        LineOption(
            name="sip",
            title="A number of my own",
            summary="I get my own phone line, and your number can forward to it.",
            cost="a dollar or two a month, plus pennies a minute",
            catch="It works even if your phone is off, lost, or in another room.",
            capabilities=sip.capabilities,
            keeps_your_number=False,
            local_audio=True,
            standalone=True,
            missing=sip.missing,
            action=sip.action,
        ),
        LineOption(
            name="vm_voip",
            title="A calling app on this PC",
            summary=(
                "I run Android here on your machine and call through an app "
                "such as Google Voice."
            ),
            cost="nothing recurring",
            catch=(
                "No phone involved and nothing to stay in range of, but the "
                "number belongs to a cloud service rather than to you."
            ),
            capabilities=vm.capabilities,
            keeps_your_number=False,
            local_audio=False,
            standalone=True,
            missing=vm.missing,
            action=vm.action,
        ),
        LineOption(
            name="phone_wired",
            title="Your own phone, on a cable",
            summary="Your real number and SIM, with a cable carrying the sound.",
            cost="about ten dollars once, for the adapter",
            catch=(
                "It is genuinely your number, but the phone has to stay plugged "
                "in here."
            ),
            capabilities=wired.capabilities,
            keeps_your_number=True,
            local_audio=True,
            standalone=False,
            missing=wired.missing,
            action=wired.action,
        ),
        LineOption(
            name="bluetooth_hfp",
            title="Your own phone, wirelessly",
            summary="The same as the cable, without the cable.",
            cost="nothing, if this PC has Bluetooth",
            catch=(
                "The one option that can drop out: the phone has to stay within "
                "a few metres and charged, so I would not rely on it."
            ),
            capabilities=bt.capabilities,
            keeps_your_number=True,
            local_audio=True,
            standalone=False,
            achievable=bt_possible,
            missing=bt.missing,
            action=bt.action,
        ),
    ]


def offer_lines(home: Path | str | None = None, *, recommend: str = "sip") -> str:
    """The spoken menu for first run, or whenever the owner asks to change it."""
    return offer(line_options(home), recommend=recommend)
