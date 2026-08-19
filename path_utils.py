"""Helpers for mapping Android file names to safe Windows paths."""

from __future__ import annotations

import hashlib
import os
import re
from typing import MutableSet, Optional


_INVALID_WINDOWS_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}


def _utf16_length(value: str) -> int:
    """Return the number of UTF-16 code units used by a Windows string."""
    return len(value.encode("utf-16-le", errors="surrogatepass")) // 2


def _truncate_utf16(value: str, max_units: int) -> str:
    """Truncate without splitting a Unicode character encoded as UTF-16."""
    result = []
    used_units = 0
    for character in value:
        character_units = _utf16_length(character)
        if used_units + character_units > max_units:
            break
        result.append(character)
        used_units += character_units
    return "".join(result)


def _add_filename_marker(
    filename: str,
    marker: str,
    max_units: int = 180,
) -> str:
    """Insert a marker before the extension while keeping it in bounds."""
    stem, suffix = os.path.splitext(filename)
    marker_units = _utf16_length(marker)
    if marker_units >= max_units:
        raise ValueError("filename marker is too long")

    # Keep at least one UTF-16 unit for the stem. Very long Android extensions
    # are shortened so collision and cache markers are never truncated away.
    suffix_budget = max_units - marker_units - 1
    suffix = _truncate_utf16(suffix, suffix_budget)
    suffix_units = _utf16_length(suffix)
    stem_budget = max_units - marker_units - suffix_units
    stem = _truncate_utf16(stem, stem_budget) or "_"
    return sanitize_windows_filename(
        f"{stem}{marker}{suffix}",
        max_length=max_units,
    )


def sanitize_windows_filename(
    filename: str,
    fallback: str = "file",
    max_length: int = 180,
) -> str:
    """Return a single Windows-safe filename.

    Android permits characters such as ``\\`` and ``:`` that Windows treats as
    separators or invalid filename characters. Replacing them here prevents a
    device filename from escaping the user-selected download directory. The
    conservative length limit also leaves room for a parent directory and a
    collision suffix on systems where long-path support is disabled.
    """
    if max_length <= 0:
        raise ValueError("max_length must be greater than zero")

    value = str(filename or "")
    value = _INVALID_WINDOWS_FILENAME_CHARS.sub("_", value)
    value = value.strip().rstrip(". ")

    if not value or value in {".", ".."}:
        value = fallback

    # Windows reserves these names even when an extension is present.
    device_name = value.split(".", 1)[0].rstrip(" .").upper()
    if device_name in _WINDOWS_RESERVED_NAMES:
        value = f"_{value}"

    if _utf16_length(value) > max_length:
        stem, suffix = os.path.splitext(value)
        suffix_length = _utf16_length(suffix)
        if suffix and suffix_length < max_length:
            stem_length = max_length - suffix_length
            value = f"{_truncate_utf16(stem, stem_length)}{suffix}"
        else:
            value = _truncate_utf16(value, max_length)
        value = value.rstrip(". ")

    return value or fallback


def available_download_path(
    directory: str,
    filename: str,
    reserved_paths: Optional[MutableSet[str]] = None,
) -> str:
    """Return a safe, non-existing path below ``directory``.

    Existing files are never selected. ``reserved_paths`` lets a batch reserve
    names before downloads finish, so two Android files that share a name do
    not overwrite each other.
    """
    base_directory = os.path.abspath(directory)
    safe_name = sanitize_windows_filename(filename)
    reserved = reserved_paths if reserved_paths is not None else set()

    counter = 0
    while True:
        if counter == 0:
            candidate_name = safe_name
        else:
            collision_suffix = f" ({counter})"
            candidate_name = _add_filename_marker(
                safe_name,
                collision_suffix,
            )

        candidate = os.path.abspath(os.path.join(base_directory, candidate_name))
        candidate_key = os.path.normcase(candidate)
        if candidate_key not in reserved and not os.path.lexists(candidate):
            reserved.add(candidate_key)
            return candidate
        counter += 1


def cached_download_path(
    directory: str,
    filename: str,
    identity: str,
) -> str:
    """Return a deterministic safe cache path for an Android file.

    The identity hash prevents equal filenames from different device folders
    (or different devices) from sharing the same temporary Windows file.
    """
    base_directory = os.path.abspath(directory)
    safe_name = sanitize_windows_filename(filename)
    digest = hashlib.sha256(
        identity.encode("utf-8", errors="surrogatepass")
    ).hexdigest()[:12]
    cache_suffix = f"-{digest}"
    cache_name = _add_filename_marker(safe_name, cache_suffix)
    return os.path.abspath(os.path.join(base_directory, cache_name))
