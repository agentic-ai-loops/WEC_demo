"""PID files recording which process runs which server (under `Settings.run_dir`)."""

import os
import signal
import time
from pathlib import Path


class PidFile:
    def __init__(self, run_dir: Path, name: str) -> None:
        self.name = name
        self.path = run_dir / f"{name}.pid"

    def read(self) -> int | None:
        """PID of the live process recorded for this server; clears stale files."""
        try:
            pid = int(self.path.read_text().strip())
        except (FileNotFoundError, ValueError):
            return None
        if _alive(pid):
            return pid
        self.path.unlink(missing_ok=True)
        return None

    def write(self, pid: int | None = None) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(f"{pid or os.getpid()}\n")

    def remove_if_mine(self) -> None:
        """Remove the file only if it still records the current process."""
        try:
            if int(self.path.read_text().strip()) == os.getpid():
                self.path.unlink()
        except (FileNotFoundError, ValueError):
            pass


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def terminate(pid: int, timeout: float = 10.0) -> bool:
    """SIGTERM `pid`, escalating to SIGKILL after `timeout`. Returns True if it exited."""
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return True
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _alive(pid):
            return True
        time.sleep(0.1)
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        return True
    time.sleep(0.2)
    return not _alive(pid)
