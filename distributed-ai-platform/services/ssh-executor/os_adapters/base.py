"""Base class and shared types for OS adapters."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from resource_spec import ResourceSpec


@dataclass
class NodeProbe:
    """Normalized result of probing a node's hardware/runtime over SSH."""

    detected_cpu: int = 0
    detected_memory_gb: float = 0.0
    detected_gpu: int = 0
    os_info: str | None = None


class OSAdapter(ABC):
    """Per-OS behaviour: probe commands, confinement, and process control.

    Adapters never open connections. They return command strings for the SSH
    transport to run and parse the raw output back into normalized values.
    """

    #: Normalized OS identifier, e.g. ``"linux"``.
    os_type: str = "unknown"

    #: Whether this OS can *enforce* a CPU/memory allocation (``"strict"``) or
    #: only approximate it with priority + monitoring (``"best_effort"``).
    resource_enforcement: str = "best_effort"

    # -- runtime basics ---------------------------------------------------
    @abstractmethod
    def python_command(self) -> str:
        """Interpreter invocation, e.g. ``python3`` or ``python``."""

    @abstractmethod
    def temp_directory(self) -> str:
        """Base directory for ephemeral per-execution files."""

    # -- detection: command + parser pairs --------------------------------
    @abstractmethod
    def probe_commands(self) -> dict[str, str]:
        """Map of probe name -> shell command (cpu, memory, gpu, os_info)."""

    @abstractmethod
    def parse_probe(self, raw: dict[str, str]) -> NodeProbe:
        """Turn raw probe stdout (keyed as in :meth:`probe_commands`) into a
        :class:`NodeProbe`."""

    # -- execution --------------------------------------------------------
    @abstractmethod
    def build_execute_command(
        self, remote_path: str, spec: ResourceSpec, *, nice: int = 19, strict: bool = False
    ) -> str:
        """Full command that runs ``remote_path`` confined to ``spec``.

        ``nice`` sets the process priority nudge (adapters that have no notion of
        it may ignore the value). ``strict`` opts into the OS's hard-enforcement
        path where one exists.
        """
