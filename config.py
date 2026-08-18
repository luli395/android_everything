"""
Configuration for Android Everything app.
"""
import os
import shutil
import sys
from typing import Optional

from version import __version__

APP_NAME = "AndroidEverything"
DISPLAY_NAME = "Android Everything"

# ADB Configuration
def get_runtime_dir() -> str:
    """Return the source directory or the packaged executable directory."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def find_adb_path(runtime_dir: Optional[str] = None) -> str:
    """Locate ADB using explicit, packaged, and PATH-based locations."""
    override = os.environ.get("ANDROID_EVERYTHING_ADB")
    if override:
        return os.path.abspath(os.path.expanduser(override))

    base_dir = runtime_dir or get_runtime_dir()
    packaged_candidates = [
        os.path.join(base_dir, "adb.exe"),
        os.path.join(base_dir, "platform-tools", "adb.exe"),
    ]
    for candidate in packaged_candidates:
        if os.path.isfile(candidate):
            return candidate

    return shutil.which("adb") or "adb"


ADB_PATH = find_adb_path()

# Default paths to scan on Android device
SCAN_PATHS = [
    "/sdcard",
    "/storage/emulated/0",
]

# Database configuration
def get_app_data_dir() -> str:
    """Return the per-user directory used for persistent application data."""
    override = os.environ.get("ANDROID_EVERYTHING_DATA_DIR")
    if override:
        return os.path.abspath(os.path.expanduser(override))

    if os.name == "nt":
        base_dir = os.environ.get("LOCALAPPDATA")
        if not base_dir:
            base_dir = os.path.join(os.path.expanduser("~"), "AppData", "Local")
    else:
        base_dir = os.environ.get("XDG_DATA_HOME")
        if not base_dir:
            base_dir = os.path.join(os.path.expanduser("~"), ".local", "share")

    return os.path.join(base_dir, APP_NAME)


APP_DATA_DIR = get_app_data_dir()
DATABASE_PATH = os.path.join(APP_DATA_DIR, "files.db")
LOG_PATH = os.path.join(APP_DATA_DIR, "android-everything.log")

# UI Configuration
WINDOW_TITLE = f"{DISPLAY_NAME} {__version__}"
WINDOW_WIDTH = 1200
WINDOW_HEIGHT = 800

# Theme colors (dark theme)
COLORS = {
    "bg_dark": "#1a1a2e",
    "bg_medium": "#16213e",
    "bg_light": "#0f3460",
    "accent": "#e94560",
    "text_primary": "#ffffff",
    "text_secondary": "#a0a0a0",
    "border": "#2a2a4a",
    "success": "#00d26a",
    "warning": "#ffc107",
    "error": "#ff4757",
}

# Search settings
SEARCH_DELAY_MS = 150  # Delay before searching after typing stops
MAX_RESULTS_DISPLAY = 10000  # Maximum results to display in list

# File type icons (emoji for simplicity)
FILE_ICONS = {
    # Documents
    ".pdf": "📄",
    ".doc": "📝",
    ".docx": "📝",
    ".txt": "📃",
    ".md": "📃",
    # Images
    ".jpg": "🖼️",
    ".jpeg": "🖼️",
    ".png": "🖼️",
    ".gif": "🖼️",
    ".bmp": "🖼️",
    ".webp": "🖼️",
    # Videos
    ".mp4": "🎬",
    ".mkv": "🎬",
    ".avi": "🎬",
    ".mov": "🎬",
    ".webm": "🎬",
    # Audio
    ".mp3": "🎵",
    ".wav": "🎵",
    ".flac": "🎵",
    ".aac": "🎵",
    ".ogg": "🎵",
    # Archives
    ".zip": "📦",
    ".rar": "📦",
    ".7z": "📦",
    ".tar": "📦",
    ".gz": "📦",
    # Code
    ".py": "🐍",
    ".js": "📜",
    ".html": "🌐",
    ".css": "🎨",
    ".json": "📋",
    ".xml": "📋",
    # APK
    ".apk": "📱",
    # Default
    "default": "📄",
    "folder": "📁",
}


def get_file_icon(filename: str, is_dir: bool = False) -> str:
    """Get the icon for a file based on its extension."""
    if is_dir:
        return FILE_ICONS["folder"]
    
    ext = os.path.splitext(filename)[1].lower()
    return FILE_ICONS.get(ext, FILE_ICONS["default"])
