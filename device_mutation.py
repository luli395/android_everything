"""Serialize operations that change one Android device or its index."""

from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Dict, Iterable, List


class DeviceMutationCoordinator:
    """Provide one mutation lock per explicit Android device serial."""

    def __init__(self):
        self._registry_lock = threading.Lock()
        self._device_locks: Dict[str, threading.RLock] = {}

    def acquire(self, device_serial: str) -> threading.RLock:
        """Acquire and return the mutation lock for ``device_serial``."""
        if not device_serial or not device_serial.strip():
            raise ValueError("A device serial is required for a mutation")

        with self._registry_lock:
            lock = self._device_locks.setdefault(
                device_serial,
                threading.RLock(),
            )

        lock.acquire()
        return lock

    @staticmethod
    def release(lock: threading.RLock):
        """Release a lock returned by :meth:`acquire`."""
        lock.release()

    @contextmanager
    def mutation(self, device_serial: str):
        """Hold the per-device mutation lock for a complete operation."""
        lock = self.acquire(device_serial)
        try:
            yield
        finally:
            self.release(lock)


def delete_device_paths(
    adb,
    db,
    coordinator: DeviceMutationCoordinator,
    device_serial: str,
    paths: Iterable[str],
) -> List[str]:
    """Delete remote paths and their index rows as one serialized mutation.

    The lock covers both the ADB deletion and the database update. Therefore an
    index refresh either commits before this deletion, after which its rows are
    removed, or scans after the deletion and never observes those files.
    """
    deleted_paths = []
    with coordinator.mutation(device_serial):
        for path in paths:
            if adb.delete_file(path, device_serial=device_serial):
                deleted_paths.append(path)

        if deleted_paths:
            db.delete_files(device_serial, deleted_paths)

    return deleted_paths


_coordinator = DeviceMutationCoordinator()


def get_device_mutation_coordinator() -> DeviceMutationCoordinator:
    """Return the process-wide device mutation coordinator."""
    return _coordinator
