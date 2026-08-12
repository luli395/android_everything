"""
Configuration for Android Everything app.
"""
import os
import shutil

# ADB Configuration
ADB_PATH = os.environ.get("ANDROID_EVERYTHING_ADB") or shutil.which("adb") or "adb"

# Default paths to scan on Android device
SCAN_PATHS = [
    "/sdcard",
    "/storage/emulated/0",
]

# Database configuration
APP_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE_PATH = os.path.join(APP_DIR, "files.db")

# UI Configuration
WINDOW_TITLE = "Android Everything"
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
