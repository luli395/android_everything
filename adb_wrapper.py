"""
ADB wrapper for communicating with Android devices.
"""
import subprocess
import os
import re
import shlex
import shutil
from typing import List, Optional, Callable
from dataclasses import dataclass
from datetime import datetime

from config import ADB_PATH


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
        self.current_device: Optional[str] = None
        
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
    
    def _device_args(self) -> List[str]:
        """Get device-specific arguments if a device is selected."""
        if self.current_device:
            return ["-s", self.current_device]
        return []
    
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
    
    def select_device(self, serial: str) -> None:
        """Select a device for subsequent commands."""
        self.current_device = serial
    
    def shell(self, command: str, timeout: int = 60) -> str:
        """
        Execute a shell command on the device.
        
        Args:
            command: Shell command to execute
            timeout: Command timeout in seconds
            
        Returns:
            Command output
        """
        args = self._device_args() + ["shell", command]
        result = self._run_command(args, timeout=timeout)
        return result.stdout
    
    def list_files_fast(self, path: str, progress_callback: Optional[Callable[[int], None]] = None) -> List[FileInfo]:
        """
        Fast file listing using 'find' command.
        
        Args:
            path: Path to scan
            progress_callback: Optional callback for progress updates
            
        Returns:
            List of FileInfo objects
        """
        # Use find with stat-like output for efficiency
        # Format: type|size|mtime|path
        cmd = f'find {shlex.quote(path)} -type f 2>/dev/null | head -100000'
        
        output = self.shell(cmd, timeout=120)
        
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
    
    def list_files_detailed(self, path: str) -> List[FileInfo]:
        """
        List files with detailed information using 'ls -la'.
        Slower but includes size and date.
        
        Args:
            path: Path to list
            
        Returns:
            List of FileInfo objects
        """
        cmd = f'ls -la {shlex.quote(path)} 2>/dev/null'
        output = self.shell(cmd)
        
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
    
    def pull_file(self, remote_path: str, local_path: str) -> bool:
        """
        Download a file from the device.
        
        Args:
            remote_path: Path on device
            local_path: Path on PC
            
        Returns:
            True if successful
        """
        args = self._device_args() + ["pull", remote_path, local_path]
        try:
            result = self._run_command(args, timeout=300, check=False)
        except ADBError:
            return False

        return result.returncode == 0
    
    def delete_file(self, remote_path: str) -> bool:
        """
        Delete a file on the device.
        
        Args:
            remote_path: Path on device
            
        Returns:
            True if successful
        """
        if not remote_path or not remote_path.startswith("/"):
            return False

        try:
            self.shell(f"rm -f {shlex.quote(remote_path)} 2>&1")
            return True
        except ADBError:
            return False
    
    def get_storage_info(self) -> dict:
        """
        Get storage information from device.
        
        Returns:
            Dict with total, used, available space in bytes
        """
        output = self.shell("df -h /sdcard 2>/dev/null | tail -1")
        
        # Parse df output
        parts = output.split()
        if len(parts) >= 4:
            return {
                "total": parts[1],
                "used": parts[2],
                "available": parts[3],
            }
        
        return {"total": "?", "used": "?", "available": "?"}
    
    def get_storage_paths(self) -> List[str]:
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
            output = self.shell("ls -d /storage/*/ 2>/dev/null")
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
            output = self.shell("ls -d /mnt/media_rw/*/ 2>/dev/null")
            for line in output.strip().split('\n'):
                line = line.strip().rstrip('/')
                if line and line.startswith('/mnt/'):
                    paths.add(line)
        except ADBError:
            pass
        
        # Check /mnt/sdcard (legacy)
        try:
            output = self.shell("ls -d /mnt/sdcard 2>/dev/null")
            if output.strip() and 'No such file' not in output:
                paths.add("/mnt/sdcard")
        except ADBError:
            pass
        
        # Check /mnt/extSdCard (Samsung legacy)
        try:
            output = self.shell("ls -d /mnt/extSdCard 2>/dev/null")
            if output.strip() and 'No such file' not in output:
                paths.add("/mnt/extSdCard")
        except ADBError:
            pass
        
        # Check /mnt/usb_storage (USB OTG)
        try:
            output = self.shell("ls -d /mnt/usb_storage/*/ 2>/dev/null")
            for line in output.strip().split('\n'):
                line = line.strip().rstrip('/')
                if line and line.startswith('/mnt/usb'):
                    paths.add(line)
        except ADBError:
            pass
        
        # Check /data/media/0 (internal on some devices, may need root)
        try:
            output = self.shell("ls -d /data/media/0 2>/dev/null")
            if output.strip() and 'Permission denied' not in output and 'No such file' not in output:
                paths.add("/data/media/0")
        except ADBError:
            pass
        
        # Check environment variable for external storage
        try:
            output = self.shell("echo $EXTERNAL_STORAGE")
            ext = output.strip()
            if ext and ext.startswith('/'):
                paths.add(ext)
        except ADBError:
            pass
        
        # Check secondary storage environment variable
        try:
            output = self.shell("echo $SECONDARY_STORAGE")
            sec = output.strip()
            if sec and sec.startswith('/'):
                for p in sec.split(':'):
                    if p:
                        paths.add(p)
        except ADBError:
            pass
        
        return list(paths)


# Singleton instance
_adb: Optional[ADBWrapper] = None


def get_adb() -> ADBWrapper:
    """Get the global ADB wrapper instance."""
    global _adb
    if _adb is None:
        _adb = ADBWrapper()
    return _adb
