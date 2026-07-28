"""In-house computer use: browser rail + full desktop (provider-agnostic).

Tools are Remedy-native so any chat model can drive the machine. Hybrid
routing prefers the in-app WebView2 for web tasks and escalates to OS
input/capture when the task needs the rest of the desktop.
"""

from __future__ import annotations

from remedy.core.computer.executor import ComputerExecutor, get_computer_executor
from remedy.core.computer.router import ComputerTarget, resolve_target
from remedy.core.computer.types import COMPUTER_TOOL_NAMES, ComputerAction

__all__ = [
    "COMPUTER_TOOL_NAMES",
    "ComputerAction",
    "ComputerExecutor",
    "ComputerTarget",
    "get_computer_executor",
    "resolve_target",
]
