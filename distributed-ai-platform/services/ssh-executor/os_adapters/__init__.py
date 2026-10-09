"""OS abstraction for agentless SSH execution.

Each adapter knows how one operating system is probed and how a task is confined
and launched on it. The adapters are pure (they *build command strings* and
*parse command output*); they do no I/O themselves, so the SSH transport stays
OS-agnostic and every adapter is unit-testable without a real machine.

Note on the package name: this package is called ``os_adapters`` rather than
``os`` on purpose. The ssh-executor service puts its own directory on
``sys.path`` and imports flatly (``import ssh_client``), so a top-level ``os``
package would shadow Python's standard-library ``os`` module for every sibling.
"""
from __future__ import annotations

from .base import NodeProbe, OSAdapter
from .linux import LinuxAdapter
from .macos import MacOSAdapter
from .windows import WindowsAdapter

__all__ = [
    "NodeProbe",
    "OSAdapter",
    "LinuxAdapter",
    "MacOSAdapter",
    "WindowsAdapter",
    "get_adapter",
    "detect_adapter",
]

_ADAPTERS: dict[str, type[OSAdapter]] = {
    "linux": LinuxAdapter,
    "macos": MacOSAdapter,
    "windows": WindowsAdapter,
}


def get_adapter(os_type: str) -> OSAdapter:
    """Return an adapter instance for a normalized os type (default: linux)."""
    cls = _ADAPTERS.get((os_type or "linux").strip().lower(), LinuxAdapter)
    return cls()


def detect_adapter(os_info: str | None) -> OSAdapter:
    """Pick an adapter from a free-form OS string (``uname``/``PRETTY_NAME``).

    Falls back to Linux, which is the baseline target for the platform.
    """
    text = (os_info or "").lower()
    if "darwin" in text or "mac os" in text or "macos" in text:
        return MacOSAdapter()
    if "windows" in text or "microsoft" in text or "mingw" in text:
        return WindowsAdapter()
    return LinuxAdapter()
