"""
ADB wrapper for communicating with Android devices.
"""
import subprocess
import os
import posixpath
import re
import shlex
import shutil
from typing import Callable, Dict, Iterable, List, Optional, Tuple
from dataclasses import dataclass
from datetime import datetime

from config import ADB_PATH


STORAGE_PATH_MARKER = "__ANDROID_EVERYTHING_STORAGE_PATH__="


@dataclass
class DeviceInfo:
    """Information about a connected Android device."""
    serial: str
    state: str  # device, offline, unauthorized
    model: str = ""
    product: str = ""


@dataclass
class FileInfo:
    """Information about a file on the Android device."""
    name: str
    path: str
    size: int  # bytes
    modified: Optional[datetime]
    is_dir: bool
    permissions: str = ""


class ADBError(Exception):
    """Exception raised when ADB command fails."""
    pass


class ADBWrapper:
    """Wrapper for ADB commands."""
    
    def __init__(self, adb_path: str = ADB_PATH):
        self.adb_path = adb_path
        
        if not os.path.isfile(self.adb_path) and not shutil.which(self.adb_path):
            raise ADBError(
                "ADB was not found. Install Android Platform Tools and add adb "
                "to PATH, or set ANDROID_EVERYTHING_ADB to the adb executable."
            )
    
    def _run_command(
        self,
        args: List[str],
        timeout: int = 30,
        check: bool = True,
    ) -> subprocess.CompletedProcess:
        """
        Run an ADB command and return its completed process information.
        
        Args:
            args: Command arguments (without 'adb' prefix)
            timeout: Command timeout in seconds
            check: Raise ADBError when ADB returns a non-zero exit code
            
        Returns:
            Completed process containing stdout, stderr, and return code
        """
        cmd = [self.adb_path] + args
        
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                encoding='utf-8',
                errors='replace'
            )
        except subprocess.TimeoutExpired as error:
            raise ADBError(
                f"ADB command timed out after {timeout} seconds: "
                f"{' '.join(args)}"
            ) from error
        except Exception as error:
            raise ADBError(f"Could not run ADB command: {error}") from error

        if check and result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            if len(detail) > 500:
                detail = detail[:497] + "..."

            message = f"ADB command failed with exit code {result.returncode}"
            if detail:
                message += f": {detail}"
            raise ADBError(message)

        return result
    
    def _device_args(self, device_serial: str) -> List[str]:
        """Return arguments that bind a command to one explicit device."""
        if not device_serial or not device_serial.strip():
            raise ADBError("A device serial is required for this ADB command")
        return ["-s", device_serial]
    
    def get_devices(self) -> List[DeviceInfo]:
        """
        Get list of connected devices.
        
        Returns:
            List of DeviceInfo objects
        """
        result = self._run_command(["devices", "-l"])
        stdout = result.stdout
        
        devices = []
        for line in stdout.strip().split('\n')[1:]:  # Skip header
            if not line.strip():
                continue
            
            parts = line.split()
            if len(parts) >= 2:
                serial = parts[0]
                state = parts[1]
                
                # Parse additional info
                model = ""
                product = ""
                for part in parts[2:]:
                    if part.startswith("model:"):
                        model = part.split(":", 1)[1]
                    elif part.startswith("product:"):
                        product = part.split(":", 1)[1]
                
                devices.append(DeviceInfo(
                    serial=serial,
                    state=state,
                    model=model,
                    product=product
                ))
        
        return devices
    
    def shell(
        self,
        command: str,
        timeout: int = 60,
        *,
        device_serial: str,
    ) -> str:
        """
        Execute a shell command on the device.
        
        Args:
            command: Shell command to execute
            timeout: Command timeout in seconds
            device_serial: Device that must receive the command
            
        Returns:
            Command output
        """
        args = self._device_args(device_serial) + ["shell", command]
        result = self._run_command(args, timeout=timeout)
        return result.stdout
    
    def list_files_fast(
        self,
        path: str,
        progress_callback: Optional[Callable[[int], None]] = None,
        *,
        device_serial: str,
    ) -> List[FileInfo]:
        """
        Fast file listing using 'find' command.
        
        Args:
            path: Path to scan
            progress_callback: Optional callback for progress updates
            device_serial: Device whose files should be listed
            
        Returns:
            List of FileInfo objects
        """
        # Use find with stat-like output for efficiency
        # Format: type|size|mtime|path
        cmd = f'find {shlex.quote(path)} -type f 2>/dev/null | head -100000'
        
        output = self.shell(
            cmd,
            timeout=120,
            device_serial=device_serial,
        )
        
        files = []
        lines = output.strip().split('\n')
        total = len(lines)
        
        for i, line in enumerate(lines):
            line = line.strip()
            if not line or line.startswith("find:"):
                continue
            
            # Get file info
            name = os.path.basename(line)
            files.append(FileInfo(
                name=name,
                path=line,
                size=0,  # Size will be fetched separately if needed
                modified=None,
                is_dir=False
            ))
            
            if progress_callback and i % 1000 == 0:
                progress_callback(int(i / total * 100))
        
        return files
    
    def list_files_detailed(
        self,
        path: str,
        *,
        device_serial: str,
    ) -> List[FileInfo]:
        """
        List files with detailed information using 'ls -la'.
        Slower but includes size and date.
        
        Args:
            path: Path to list
            device_serial: Device whose files should be listed
            
        Returns:
            List of FileInfo objects
        """
        cmd = f'ls -la {shlex.quote(path)} 2>/dev/null'
        output = self.shell(cmd, device_serial=device_serial)
        
        files = []
        for line in output.strip().split('\n'):
            if not line or line.startswith("total"):
                continue
            
            # Parse ls -la output
            # Example: -rw-rw---- 1 u0_a123 u0_a123 12345 2024-01-15 10:30 filename.txt
            parts = line.split(None, 7)
            if len(parts) < 8:
                continue
            
            permissions = parts[0]
            is_dir = permissions.startswith('d')
            
            try:
                size = int(parts[4])
            except ValueError:
                size = 0
            
            # Parse date
            try:
                date_str = f"{parts[5]} {parts[6]}"
                modified = datetime.strptime(date_str, "%Y-%m-%d %H:%M")
            except (ValueError, IndexError):
                modified = None
            
            name = parts[7]
            if name in ['.', '..']:
                continue
            
            full_path = os.path.join(path, name).replace('\\', '/')
            
            files.append(FileInfo(
                name=name,
                path=full_path,
                size=size,
                modified=modified,
                is_dir=is_dir,
                permissions=permissions
            ))
        
        return files
    
    def pull_file(
        self,
        remote_path: str,
        local_path: str,
        *,
        device_serial: str,
    ) -> bool:
        """
        Download a file from the device.
        
        Args:
            remote_path: Path on device
            local_path: Path on PC
            device_serial: Device to download from
            
        Returns:
            True if successful
        """
        args = self._device_args(device_serial) + [
            "pull",
            remote_path,
            local_path,
        ]
        try:
            result = self._run_command(args, timeout=300, check=False)
        except ADBError:
            return False

        return result.returncode == 0
    
    def delete_file(
        self,
        remote_path: str,
        *,
        device_serial: str,
    ) -> bool:
        """
        Delete a file on the device.
        
        Args:
            remote_path: Path on device
            device_serial: Device to delete from
            
        Returns:
            True if successful
        """
        if not remote_path or not remote_path.startswith("/"):
            return False

        try:
            self.shell(
                f"rm -f {shlex.quote(remote_path)} 2>&1",
                device_serial=device_serial,
            )
            return True
        except ADBError:
            return False
    
    def get_storage_info(self, *, device_serial: str) -> dict:
        """
        Get storage information from device.
        
        Returns:
            Dict with total, used, available space in bytes
        """
        output = self.shell(
            "df -h /sdcard 2>/dev/null | tail -1",
            device_serial=device_serial,
        )
        
        # Parse df output
        parts = output.split()
        if len(parts) >= 4:
            return {
                "total": parts[1],
                "used": parts[2],
                "available": parts[3],
            }
        
        return {"total": "?", "used": "?", "available": "?"}
    
    def get_storage_paths(self, *, device_serial: str) -> List[str]:
        """
        Detect all available storage paths including SD card and USB storage.
        
        Returns:
            List of storage paths to scan
        """
        paths = set()
        
        # Primary internal storage
        paths.add("/storage/emulated/0")
        
        # Detect external/SD card storage from /storage
        try:
            output = self.shell(
                "ls -d /storage/*/ 2>/dev/null",
                device_serial=device_serial,
            )
            for line in output.strip().split('\n'):
                line = line.strip().rstrip('/')
                if not line:
                    continue
                # Skip emulated (internal) and self
                if 'emulated' in line or 'self' in line:
                    continue
                if line.startswith('/storage/'):
                    paths.add(line)
        except ADBError:
            pass
        
        # Check /mnt/media_rw (SD cards on some devices)
        try:
            output = self.shell(
                "ls -d /mnt/media_rw/*/ 2>/dev/null",
                device_serial=device_serial,
            )
            for line in output.strip().split('\n'):
                line = line.strip().rstrip('/')
                if line and line.startswith('/mnt/'):
                    paths.add(line)
        except ADBError:
            pass
        
        # Check /mnt/sdcard (legacy)
        try:
            output = self.shell(
                "ls -d /mnt/sdcard 2>/dev/null",
                device_serial=device_serial,
            )
            if output.strip() and 'No such file' not in output:
                paths.add("/mnt/sdcard")
        except ADBError:
            pass
        
        # Check /mnt/extSdCard (Samsung legacy)
        try:
            output = self.shell(
                "ls -d /mnt/extSdCard 2>/dev/null",
                device_serial=device_serial,
            )
            if output.strip() and 'No such file' not in output:
                paths.add("/mnt/extSdCard")
        except ADBError:
            pass
        
        # Check /mnt/usb_storage (USB OTG)
        try:
            output = self.shell(
                "ls -d /mnt/usb_storage/*/ 2>/dev/null",
                device_serial=device_serial,
            )
            for line in output.strip().split('\n'):
                line = line.strip().rstrip('/')
                if line and line.startswith('/mnt/usb'):
                    paths.add(line)
        except ADBError:
            pass
        
        # Check /data/media/0 (internal on some devices, may need root)
        try:
            output = self.shell(
                "ls -d /data/media/0 2>/dev/null",
                device_serial=device_serial,
            )
            if output.strip() and 'Permission denied' not in output and 'No such file' not in output:
                paths.add("/data/media/0")
        except ADBError:
            pass
        
        # Check environment variable for external storage
        try:
            output = self.shell(
                "echo $EXTERNAL_STORAGE",
                device_serial=device_serial,
            )
            ext = output.strip()
            if ext and ext.startswith('/'):
                paths.add(ext)
        except ADBError:
            pass
        
        # Check secondary storage environment variable
        try:
            output = self.shell(
                "echo $SECONDARY_STORAGE",
                device_serial=device_serial,
            )
            sec = output.strip()
            if sec and sec.startswith('/'):
                for p in sec.split(':'):
                    if p:
                        paths.add(p)
        except ADBError:
            pass
        
        return self._deduplicate_storage_paths(
            paths,
            device_serial=device_serial,
        )

    @staticmethod
    def _normalize_storage_path(path: str) -> Optional[str]:
        """Return a safe normalized absolute Android storage path."""
        if not isinstance(path, str):
            return None

        path = path.strip()
        if (
            not path.startswith("/")
            or path == "/"
            or any(ord(character) < 32 for character in path)
        ):
            return None

        normalized = posixpath.normpath(path)
        if normalized == "/" or not normalized.startswith("/"):
            return None
        return normalized

    @staticmethod
    def _well_known_storage_key(path: str) -> Optional[str]:
        """Map Android's common storage aliases to one logical volume key."""
        primary_aliases = {
            "/data/media/0",
            "/mnt/sdcard",
            "/sdcard",
            "/storage/emulated/0",
            "/storage/self/primary",
        }
        if path in primary_aliases:
            return "primary:0"

        match = re.fullmatch(r"/storage/emulated/([^/]+)", path)
        if match:
            return f"emulated:{match.group(1)}"

        match = re.fullmatch(r"/data/media/([^/]+)", path)
        if match:
            return f"emulated:{match.group(1)}"

        for prefix in (
            "/storage/",
            "/mnt/media_rw/",
            "/mnt/usb_storage/",
        ):
            if path.startswith(prefix):
                volume_name = path[len(prefix):]
                if volume_name and "/" not in volume_name:
                    if volume_name not in ("emulated", "self"):
                        return f"volume:{volume_name.casefold()}"

        return None

    @classmethod
    def _storage_alias_tokens(
        cls,
        path: str,
        resolved_path: str,
        identity: str,
    ) -> set:
        """Return all identities that can connect equivalent mount points."""
        tokens = {f"resolved:{resolved_path}"}
        for candidate in (path, resolved_path):
            known_key = cls._well_known_storage_key(candidate)
            if known_key:
                tokens.add(f"known:{known_key}")
        if identity:
            tokens.add(f"identity:{identity}")
        return tokens

    @staticmethod
    def _storage_path_priority(path: str) -> Tuple[int, str]:
        """Prefer stable app-visible mount points over legacy/raw aliases."""
        if path == "/storage/emulated/0":
            priority = 0
        elif re.fullmatch(r"/storage/[^/]+", path) and path not in (
            "/storage/emulated",
            "/storage/self",
        ):
            priority = 10
        elif re.fullmatch(r"/storage/emulated/[^/]+", path):
            priority = 20
        elif path == "/storage/self/primary":
            priority = 30
        elif path == "/sdcard":
            priority = 40
        elif path.startswith("/mnt/usb_storage/"):
            priority = 50
        elif path.startswith("/mnt/media_rw/"):
            priority = 60
        elif path == "/mnt/extSdCard":
            priority = 70
        elif path == "/mnt/sdcard":
            priority = 80
        elif path.startswith("/data/media/"):
            priority = 90
        else:
            priority = 35

        return priority, path.casefold()

    def _deduplicate_storage_paths(
        self,
        paths: Iterable[str],
        *,
        device_serial: str,
    ) -> List[str]:
        """Resolve storage aliases and return one preferred path per volume."""
        normalized_paths = sorted({
            normalized
            for path in paths
            for normalized in [self._normalize_storage_path(path)]
            if normalized is not None
        })
        if not normalized_paths:
            return []

        quoted_paths = " ".join(
            shlex.quote(path) for path in normalized_paths
        )
        probe_command = (
            f"for storage_path in {quoted_paths}; do "
            "if [ -d \"$storage_path\" ]; then "
            "resolved_path=$(readlink -f \"$storage_path\" 2>/dev/null); "
            "if [ -z \"$resolved_path\" ]; then "
            "resolved_path=\"$storage_path\"; fi; "
            "storage_identity=$(stat -c '%d:%i' \"$storage_path\" "
            "2>/dev/null); "
            f"printf '{STORAGE_PATH_MARKER}%s\\t%s\\t%s\\n' "
            "\"$storage_path\" \"$resolved_path\" "
            "\"$storage_identity\"; "
            "fi; done; exit 0"
        )

        probed_paths: Dict[str, Tuple[str, str]] = {}
        try:
            output = self.shell(
                probe_command,
                device_serial=device_serial,
            )
        except ADBError:
            # Alias probing is an optimization. Preserve storage discovery on
            # older devices while still collapsing the well-known aliases.
            probed_paths = {
                path: (path, "") for path in normalized_paths
            }
        else:
            for line in output.splitlines():
                if not line.startswith(STORAGE_PATH_MARKER):
                    continue

                fields = line[len(STORAGE_PATH_MARKER):].split("\t")
                if len(fields) != 3:
                    continue

                path = self._normalize_storage_path(fields[0])
                resolved_path = self._normalize_storage_path(fields[1])
                identity = fields[2].strip()
                if path not in normalized_paths or resolved_path is None:
                    continue
                if identity and not re.fullmatch(r"[^\s:]+:[^\s:]+", identity):
                    identity = ""

                probed_paths[path] = (resolved_path, identity)

        alias_groups = []
        for path in sorted(probed_paths, key=self._storage_path_priority):
            resolved_path, identity = probed_paths[path]
            tokens = self._storage_alias_tokens(
                path,
                resolved_path,
                identity,
            )

            matching_groups = [
                group
                for group in alias_groups
                if group["tokens"].intersection(tokens)
            ]
            if not matching_groups:
                alias_groups.append({"tokens": set(tokens), "paths": [path]})
                continue

            primary_group = matching_groups[0]
            primary_group["tokens"].update(tokens)
            primary_group["paths"].append(path)
            for other_group in matching_groups[1:]:
                primary_group["tokens"].update(other_group["tokens"])
                primary_group["paths"].extend(other_group["paths"])
                alias_groups.remove(other_group)

        selected_paths = [
            min(group["paths"], key=self._storage_path_priority)
            for group in alias_groups
        ]

        return sorted(
            selected_paths,
            key=self._storage_path_priority,
        )


# Singleton instance
_adb: Optional[ADBWrapper] = None


def get_adb() -> ADBWrapper:
    """Get the global ADB wrapper instance."""
    global _adb
    if _adb is None:
        _adb = ADBWrapper()
    return _adb
