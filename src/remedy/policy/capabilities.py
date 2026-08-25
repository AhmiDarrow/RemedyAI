"""Authority units a tool may request. The model never grants these itself."""

from __future__ import annotations

from enum import StrEnum


class Capability(StrEnum):
    FS_READ = "fs.read"
    FS_WRITE = "fs.write"
    PROCESS_EXEC = "process.exec"
    NETWORK_READ = "network.read"
    NETWORK_WRITE = "network.write"
    BROWSER_READ = "browser.read"
    BROWSER_WRITE = "browser.write"
    COMPUTER_READ = "computer.read"
    COMPUTER_INPUT = "computer.input"
    COMMUNICATE = "communicate"
    TRANSACT = "transact"
    DELETE = "delete"
    CREDENTIAL_USE = "credential.use"


CapabilitySet = frozenset[Capability]
