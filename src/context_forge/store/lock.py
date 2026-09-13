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

_lock_registry: dict[str, threading.RLock] = {}
_registry_lock = threading.Lock()


def _get_process_rlock(lock_path: Path) -> threading.RLock:
    """Return in-process RLock specific to the target repository lock path."""
    try:
        key = str(lock_path.resolve()).replace("\\", "/").lower()
    except (OSError, RuntimeError):
        key = str(lock_path).replace("\\", "/").lower()
    with _registry_lock:
        if key not in _lock_registry:
            _lock_registry[key] = threading.RLock()
        return _lock_registry[key]


_tls = threading.local()


class LockTimeoutError(TimeoutError):
    """Raised when repository lock acquisition times out."""
    pass


import socket
import uuid


def _is_pid_alive(pid: int) -> bool:
    """Check if process with PID is currently alive on the host."""
    if pid <= 0:
        return False
    try:
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes
            kernel32 = ctypes.windll.kernel32
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            SYNCHRONIZE = 0x00100000
            h_proc = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE, False, pid)
            if not h_proc:
                err = kernel32.GetLastError()
                # ERROR_ACCESS_DENIED (5) means process exists and is alive
                return err == 5
            exit_code = wintypes.DWORD()
            if kernel32.GetExitCodeProcess(h_proc, ctypes.byref(exit_code)):
                kernel32.CloseHandle(h_proc)
                # STILL_ACTIVE = 259
                return exit_code.value == 259
            kernel32.CloseHandle(h_proc)
            return False
        else:
            os.kill(pid, 0)
            return True
    except ProcessLookupError:
        return False
    except PermissionError:
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
    """Cross-process and reentrant thread-safe lock for repository mutations.
    
    Invariants:
    1. A lock held by a confirmed alive local PID is NEVER stolen, regardless of age.
    2. A lock held by a confirmed dead PID is reclaimed safely.
    3. Corrupt or unreadable locks are reclaimed only after exceeding stale_timeout.
    """
    lock_path = get_lock_path(target)
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    # Acquire per-repository process-level RLock first
    proc_lock = _get_process_rlock(lock_path)
    acquired_thread_lock = proc_lock.acquire(timeout=timeout)
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
        nonce = uuid.uuid4().hex
        current_host = socket.gethostname()
        lock_info = {
            "pid": os.getpid(),
            "thread": threading.get_ident(),
            "hostname": current_host,
            "nonce": nonce,
            "created_at": time.time(),
            "acquired_at": time.time(),
        }

        while time.time() < deadline:
            try:
                fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_RDWR)
                os.write(fd, json.dumps(lock_info).encode("utf-8"))
                os.close(fd)
                acquired_file_lock = True
                break
            except FileExistsError:
                # Inspect existing lock
                try:
                    raw = lock_path.read_text(encoding="utf-8", errors="replace")
                    if not raw.strip():
                        # Possible concurrent write; verify file age
                        mtime = lock_path.stat().st_mtime
                        if time.time() - mtime > stale_timeout:
                            lock_path.unlink(missing_ok=True)
                            continue
                    else:
                        data = json.loads(raw)
                        lock_pid = data.get("pid", 0)
                        lock_host = data.get("hostname", "")
                        acquired_at = data.get("acquired_at") or data.get("created_at") or 0
                        age = time.time() - acquired_at

                        if not lock_host or lock_host == current_host:
                            if lock_pid and lock_pid > 0:
                                if _is_pid_alive(lock_pid):
                                    # Owner confirmed alive: lock remains valid regardless of age. NEVER STEAL!
                                    pass
                                else:
                                    # Owner confirmed dead: reclaim safely
                                    lock_path.unlink(missing_ok=True)
                                    continue
                            else:
                                if age > stale_timeout:
                                    lock_path.unlink(missing_ok=True)
                                    continue
                        else:
                            # Uncertain hostname: cautious reclamation policy
                            if age > (stale_timeout * 3):
                                lock_path.unlink(missing_ok=True)
                                continue
                except json.JSONDecodeError:
                    try:
                        mtime = lock_path.stat().st_mtime
                        if time.time() - mtime > stale_timeout:
                            lock_path.unlink(missing_ok=True)
                            continue
                    except OSError:
                        pass
                except OSError:
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
                raw = lock_path.read_text(encoding="utf-8", errors="replace")
                data = json.loads(raw) if raw else {}
                # Only unlink if this process still owns the lock
                if data.get("nonce") == nonce or data.get("pid") == os.getpid():
                    lock_path.unlink(missing_ok=True)
            except OSError:
                pass

    finally:
        proc_lock.release()
