"""Cross-process repository locking for concurrent multi-agent operations.

Concurrency Boundary:
- Guarantees cross-process and cross-thread mutual exclusion on a single host.
- Supports reentrancy within the same process thread.
- Recovers safely from stale locks (crashed agents) via PID liveness and timeout checks.
- Boundary Note: Cross-machine shared-filesystem safety (e.g. multi-node NFS/SMB)
  is not guaranteed; Context Forge does not claim distributed transactional semantics.
"""

from __future__ import annotations

import json
import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Generator, Union

_process_rlock = threading.RLock()
_tls = threading.local()


class LockTimeoutError(TimeoutError):
    """Raised when repository lock acquisition times out."""
    pass


def _is_pid_alive(pid: int) -> bool:
    """Check if process with PID is currently alive on the host."""
    if pid <= 0:
        return False
    try:
        if os.name == "nt":
            import ctypes
            kernel32 = ctypes.windll.kernel32
            SYNCHRONIZE = 0x00100000
            process = kernel32.OpenProcess(SYNCHRONIZE, False, pid)
            if process != 0:
                kernel32.CloseHandle(process)
                return True
            return False
        else:
            os.kill(pid, 0)
            return True
    except OSError:
        return False


def get_lock_path(target: Union[Path, dict[str, Path]]) -> Path:
    if isinstance(target, dict):
        root = target.get("root")
        if root is None:
            raise ValueError("Target dictionary missing 'root' path")
        return root / ".repo.lock"
    elif isinstance(target, Path):
        if (target / ".brain").exists():
            return target / ".brain" / ".repo.lock"
        elif target.name == ".brain":
            return target / ".repo.lock"
        else:
            return target / ".brain" / ".repo.lock"
    raise TypeError(f"Unsupported target type: {type(target)}")


@contextmanager
def repo_lock(
    target: Union[Path, dict[str, Path]],
    timeout: float = 15.0,
    stale_timeout: float = 30.0,
) -> Generator[Path, None, None]:
    """Cross-process and reentrant thread-safe lock for repository mutations."""
    lock_path = get_lock_path(target)
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    # Acquire process-level RLock first
    acquired_thread_lock = _process_rlock.acquire(timeout=timeout)
    if not acquired_thread_lock:
        raise LockTimeoutError(f"Thread lock acquisition timed out for {lock_path}")

    try:
        # Reentrant check for current thread
        current_depth = getattr(_tls, "depth", 0)
        current_lock = getattr(_tls, "lock_path", None)
        if current_depth > 0 and current_lock == lock_path:
            _tls.depth = current_depth + 1
            try:
                yield lock_path
            finally:
                _tls.depth -= 1
            return

        # Cross-process file lock acquisition loop
        deadline = time.time() + timeout
        acquired_file_lock = False

        while time.time() < deadline:
            try:
                fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_RDWR)
                info = {
                    "pid": os.getpid(),
                    "thread": threading.get_ident(),
                    "acquired_at": time.time(),
                }
                os.write(fd, json.dumps(info).encode("utf-8"))
                os.close(fd)
                acquired_file_lock = True
                break
            except FileExistsError:
                # Check for stale lock
                try:
                    raw = lock_path.read_text(encoding="utf-8", errors="replace")
                    data = json.loads(raw) if raw else {}
                    lock_pid = data.get("pid", 0)
                    acquired_at = data.get("acquired_at", 0)
                    age = time.time() - acquired_at

                    # If the holding PID is no longer alive, or exceeds stale_timeout
                    if (lock_pid and not _is_pid_alive(lock_pid)) or age > stale_timeout:
                        try:
                            lock_path.unlink(missing_ok=True)
                            continue
                        except OSError:
                            pass
                except Exception:
                    pass

                time.sleep(0.02)

        if not acquired_file_lock:
            raise LockTimeoutError(f"Cross-process repository lock timed out after {timeout}s: {lock_path}")

        _tls.depth = 1
        _tls.lock_path = lock_path

        try:
            yield lock_path
        finally:
            _tls.depth = 0
            _tls.lock_path = None
            try:
                lock_path.unlink(missing_ok=True)
            except OSError:
                pass

    finally:
        _process_rlock.release()
