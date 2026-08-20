"""The capability probe — the thing that replaces a setup wizard.

Its output is read aloud mid-conversation, so these tests care as much about the
sentences as about the booleans.
"""

from __future__ import annotations

import sys

import pytest

from remedy.telephony.line import Capabilities
from remedy.telephony.registry import (
    BackendProbe,
    TelephonyStatus,
    has_bluetooth_radio,
    probe_all,
    probe_bench,
)

READY = Capabilities(outbound=True, inbound=True)


def _status(**states: bool) -> TelephonyStatus:
    probes = [probe_bench()]
    for name, ok in states.items():
        probes.append(
            BackendProbe(
                name=name,
                capabilities=READY if ok else Capabilities(),
                missing=() if ok else (f"{name} is not set up",),
                action="" if ok else f"do the {name} thing",
            )
        )
    return TelephonyStatus(probes=probes)


def test_probe_never_raises_and_is_fast_enough_to_run_mid_sentence():
    status = probe_all()
    assert status.probes
    assert status.get("bench") is not None


def test_bench_is_always_available_but_is_not_a_real_call():
    status = probe_all()
    assert probe_bench().ready
    # A simulated call must never make her claim she can phone people.
    assert not any(p.name == "bench" for p in status.ready)


def test_phone_bridge_needs_both_control_and_audio():
    """ADB dials; Bluetooth carries the voice. Either alone is not a call."""
    assert not _status(android=True, bluetooth_hfp=False, sip=False).can_call
    assert not _status(android=False, bluetooth_hfp=True, sip=False).can_call
    assert _status(android=True, bluetooth_hfp=True, sip=False).can_call


def test_sip_alone_is_enough():
    assert _status(android=False, bluetooth_hfp=False, sip=True).can_call


def test_says_it_plainly_when_ready():
    assert _status(android=True, bluetooth_hfp=True, sip=False).say() == (
        "I can make and take calls."
    )


def test_counts_the_missing_things_correctly():
    """"Two things" followed by three things is the kind of small wrongness that
    makes everything else she says sound unreliable."""
    said = _status(android=False, bluetooth_hfp=False, sip=False).say()
    assert said.startswith("Not yet, two things.")
    assert "1)" in said and "2)" in said and "3)" not in said


def test_does_not_mention_sip_when_the_phone_bridge_is_the_blocker():
    """Nobody wants to hear about a SIP engine when the real blocker is a
    $10 dongle. Only the nearest working path gets named."""
    said = _status(android=False, bluetooth_hfp=False, sip=False).say()
    assert "sip" not in said.lower()


def test_single_blocker_reads_as_almost_there():
    said = _status(android=True, bluetooth_hfp=False, sip=False).say()
    assert said.startswith("Almost")
    assert "bluetooth_hfp is not set up" in said


def test_every_gap_carries_an_action_the_owner_can_take():
    """A gap with no next step is a dead end in a conversation."""
    for probe in probe_all().probes:
        if probe.missing and probe.name != "bench":
            assert probe.action or probe.detail, f"{probe.name} states a gap with no way out"


def test_spoken_status_has_no_error_codes():
    said = probe_all().say()
    for shouty in ("ERR", "None", "Traceback", "Exception", "null"):
        assert shouty not in said


@pytest.mark.skipif(sys.platform != "win32", reason="the probe only runs on Windows")
def test_the_bluetooth_probe_reaches_an_answer_on_this_machine():
    """It used to close the radio with ``bthprops.BluetoothCloseHandle``, which
    that DLL does not export — so on any PC that *does* have a radio the call
    raised, the blanket catch turned it into "I cannot tell", and she told every
    owner she could not make calls."""
    assert has_bluetooth_radio() is not None


@pytest.mark.skipif(sys.platform != "win32", reason="the probe only runs on Windows")
def test_the_symbols_the_bluetooth_probe_calls_actually_exist():
    import ctypes

    bthprops = ctypes.WinDLL("bthprops.cpl")
    kernel32 = ctypes.WinDLL("kernel32")
    for dll, symbol in (
        (bthprops, "BluetoothFindFirstRadio"),
        (bthprops, "BluetoothFindRadioClose"),
        (kernel32, "CloseHandle"),
    ):
        assert getattr(dll, symbol, None) is not None, symbol


def test_every_line_on_the_menu_is_also_probed():
    """``line_options()`` offers four ways to get a line. If ``probe_all`` does
    not probe one of them, the menu says it is ready while the status says she
    cannot call."""
    from remedy.telephony.registry import line_options

    probed = {p.name for p in probe_all().probes}
    for option in line_options():
        expected = "android_vm" if option.name == "vm_voip" else option.name
        assert expected in probed, f"{option.name} is offered but never probed"


def test_a_standalone_line_is_enough_on_its_own():
    """Each of these carries dialling and audio by itself; only the phone
    bridge needs a second half. Leaving them out made her deny a line she had."""
    for name in ("sip", "android_vm", "phone_wired"):
        assert _status(**{name: True}).can_call, name


def test_a_running_vm_with_no_account_is_not_a_phone_line():
    """The same rule as SIP: an installed engine is not a number."""
    from remedy.telephony.registry import probe_android_vm

    probe = probe_android_vm()
    if probe.detail and "running here" in probe.detail:
        assert probe.missing, "a VM with no calling app signed in reported ready"


def test_sip_finds_an_engine_it_downloaded_itself(tmp_path, monkeypatch):
    """Fetched components land in REMEDY_HOME/bin, which is not on PATH — and on
    Windows the file is baresip.exe."""
    from remedy.telephony.registry import probe_sip

    monkeypatch.setattr("shutil.which", lambda name: "")
    binned = tmp_path / "bin"
    binned.mkdir()
    assert "not installed" in probe_sip(tmp_path).missing[0]

    (binned / ("baresip.exe" if sys.platform == "win32" else "baresip")).write_text("x")
    missing = probe_sip(tmp_path).missing
    assert missing and "not installed" not in missing[0]
    assert "account" in missing[0]


def test_sip_looks_in_the_default_home_when_none_is_given(tmp_path, monkeypatch):
    from remedy.telephony.registry import probe_sip

    monkeypatch.setattr("shutil.which", lambda name: "")
    monkeypatch.setenv("REMEDY_HOME", str(tmp_path))
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / ("baresip.exe" if sys.platform == "win32" else "baresip")).write_text("x")
    assert "not installed" not in probe_sip().missing[0]


def test_probing_everything_shells_out_to_adb_once(monkeypatch):
    """``probe_all`` is read aloud mid-sentence and each adb call can cost four
    seconds. Three probes need the device list; they share one call."""
    from remedy.telephony import registry

    calls = []
    monkeypatch.setattr(registry, "_adb_path", lambda: "adb")
    monkeypatch.setattr(
        registry, "adb_devices", lambda: (calls.append(1), ([], []))[1]
    )
    registry.probe_all()
    assert len(calls) == 1, f"adb ran {len(calls)} times"

    calls.clear()
    registry.line_options()
    assert len(calls) == 1, f"adb ran {len(calls)} times"


# ---------------------------------------------------------------------------
# ctypes marshalling — the probe on a machine that HAS a radio
# ---------------------------------------------------------------------------


class _StrictFunction:
    """A ctypes foreign function as ctypes actually behaves: with no
    ``argtypes`` every Python int is marshalled as a C ``int``, and one that
    does not fit 32 bits raises ``ArgumentError``."""

    def __init__(self, name: str, impl, calls: list) -> None:
        self.name = name
        self.impl = impl
        self.calls = calls
        self.argtypes = None
        self.restype = None

    def __call__(self, *args):
        import ctypes

        if self.argtypes is None:
            for a in args:
                if isinstance(a, int) and not (-(2**31) <= a < 2**31):
                    raise ctypes.ArgumentError(
                        f"argument 1: OverflowError: int too long to convert ({self.name})"
                    )
        elif len(self.argtypes) != len(args):
            raise ctypes.ArgumentError(f"{self.name}: wrong number of arguments")
        self.calls.append((self.name, args))
        return self.impl(*args)


class _StrictDLL:
    def __init__(self, impls: dict, calls: list) -> None:
        self._fns = {n: _StrictFunction(n, f, calls) for n, f in impls.items()}

    def __getattr__(self, name: str):
        try:
            return self._fns[name]
        except KeyError:
            raise AttributeError(name) from None


_RADIO_FINDER = 0x1_0000_0010  # a handle that does not fit a C int
_RADIO_HANDLE = 0x2_0000_0020


def _install_radio_machine(monkeypatch):
    """A fake Windows with one Bluetooth radio, behind strict ctypes rules."""
    import ctypes

    calls: list = []

    def find_first(params, pradio):
        pradio._obj.value = _RADIO_HANDLE
        return _RADIO_FINDER

    dlls = {
        "bthprops.cpl": _StrictDLL(
            {
                "BluetoothFindFirstRadio": find_first,
                "BluetoothFindRadioClose": lambda h: 1,
            },
            calls,
        ),
        "kernel32": _StrictDLL({"CloseHandle": lambda h: 1}, calls),
    }
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(ctypes, "WinDLL", lambda name, **kw: dlls[name], raising=False)
    return calls


def test_a_machine_with_a_radio_is_reported_as_having_one(monkeypatch):
    """``BluetoothFindRadioClose`` had no argtypes. Once the finder's restype
    became a 64-bit HANDLE, handing it back as a C int raised ArgumentError,
    the blanket catch turned that into None — "I could not tell" — on exactly
    the machines that do have a radio."""
    calls = _install_radio_machine(monkeypatch)
    assert has_bluetooth_radio() is True
    assert ("BluetoothFindRadioClose", (_RADIO_FINDER,)) in calls


def test_the_radio_probe_closes_both_handles_it_opened(monkeypatch):
    calls = _install_radio_machine(monkeypatch)
    has_bluetooth_radio()
    names = [name for name, _ in calls]
    assert names == ["BluetoothFindFirstRadio", "CloseHandle", "BluetoothFindRadioClose"]
    close_args = dict(calls)["CloseHandle"]
    assert getattr(close_args[0], "value", close_args[0]) == _RADIO_HANDLE


def test_every_bluetooth_function_declares_handle_argtypes(monkeypatch):
    import ctypes
    from ctypes import wintypes

    _install_radio_machine(monkeypatch)
    has_bluetooth_radio()
    bth = ctypes.WinDLL("bthprops.cpl")
    k32 = ctypes.WinDLL("kernel32")
    assert bth.BluetoothFindRadioClose.argtypes == [wintypes.HANDLE]
    assert bth.BluetoothFindRadioClose.restype is wintypes.BOOL
    assert bth.BluetoothFindFirstRadio.restype is wintypes.HANDLE
    assert bth.BluetoothFindFirstRadio.argtypes is not None
    assert len(bth.BluetoothFindFirstRadio.argtypes) == 2
    assert k32.CloseHandle.argtypes == [wintypes.HANDLE]
