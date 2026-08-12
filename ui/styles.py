"""
UI styling and theme configuration.
"""
import tkinter as tk
from tkinter import ttk

# Color scheme (dark theme)
COLORS = {
    "bg_dark": "#1a1a2e",
    "bg_medium": "#16213e", 
    "bg_light": "#0f3460",
    "accent": "#e94560",
    "accent_hover": "#ff6b8a",
    "text_primary": "#ffffff",
    "text_secondary": "#a0a0a0",
    "text_muted": "#606080",
    "border": "#2a2a4a",
    "success": "#00d26a",
    "warning": "#ffc107",
    "error": "#ff4757",
    "selection": "#3a3a6a",
    "row_alt": "#1e1e3a",
}

# Fonts
FONTS = {
    "title": ("Segoe UI", 24, "bold"),
    "heading": ("Segoe UI", 14, "bold"),
    "body": ("Segoe UI", 11),
    "small": ("Segoe UI", 10),
    "mono": ("Consolas", 10),
}


def apply_dark_theme(root: tk.Tk):
    """Apply dark theme to the application."""
    style = ttk.Style()
    
    # Configure main window
    root.configure(bg=COLORS["bg_dark"])
    
    # Configure ttk styles
    style.theme_use("clam")
    
    # Frame
    style.configure(
        "TFrame",
        background=COLORS["bg_dark"]
    )
    
    style.configure(
        "Card.TFrame",
        background=COLORS["bg_medium"],
        relief="flat"
    )
    
    # Label
    style.configure(
        "TLabel",
        background=COLORS["bg_dark"],
        foreground=COLORS["text_primary"],
        font=FONTS["body"]
    )
    
    style.configure(
        "Title.TLabel",
        font=FONTS["title"],
        foreground=COLORS["accent"]
    )
    
    style.configure(
        "Heading.TLabel",
        font=FONTS["heading"]
    )
    
    style.configure(
        "Muted.TLabel",
        foreground=COLORS["text_secondary"]
    )
    
    # Entry
    style.configure(
        "TEntry",
        fieldbackground=COLORS["bg_medium"],
        foreground=COLORS["text_primary"],
        insertcolor=COLORS["text_primary"],
        bordercolor=COLORS["border"],
        lightcolor=COLORS["border"],
        darkcolor=COLORS["border"]
    )
    
    style.map(
        "TEntry",
        fieldbackground=[("focus", COLORS["bg_light"])],
        bordercolor=[("focus", COLORS["accent"])]
    )
    
    # Button
    style.configure(
        "TButton",
        background=COLORS["bg_light"],
        foreground=COLORS["text_primary"],
        bordercolor=COLORS["border"],
        focuscolor=COLORS["accent"],
        font=FONTS["body"],
        padding=(12, 6)
    )
    
    style.map(
        "TButton",
        background=[
            ("active", COLORS["accent"]),
            ("pressed", COLORS["accent_hover"])
        ],
        foreground=[("active", COLORS["text_primary"])]
    )
    
    style.configure(
        "Accent.TButton",
        background=COLORS["accent"],
        foreground=COLORS["text_primary"]
    )
    
    style.map(
        "Accent.TButton",
        background=[("active", COLORS["accent_hover"])]
    )
    
    # Combobox
    style.configure(
        "TCombobox",
        fieldbackground=COLORS["bg_medium"],
        background=COLORS["bg_light"],
        foreground=COLORS["text_primary"],
        arrowcolor=COLORS["text_primary"],
        bordercolor=COLORS["border"]
    )
    
    style.map(
        "TCombobox",
        fieldbackground=[("focus", COLORS["bg_light"])],
        bordercolor=[("focus", COLORS["accent"])]
    )
    
    # Treeview (file list)
    style.configure(
        "Treeview",
        background=COLORS["bg_medium"],
        foreground=COLORS["text_primary"],
        fieldbackground=COLORS["bg_medium"],
        bordercolor=COLORS["border"],
        font=FONTS["body"],
        rowheight=28
    )
    
    style.configure(
        "Treeview.Heading",
        background=COLORS["bg_light"],
        foreground=COLORS["text_primary"],
        font=FONTS["heading"],
        bordercolor=COLORS["border"]
    )
    
    style.map(
        "Treeview",
        background=[("selected", COLORS["selection"])],
        foreground=[("selected", COLORS["text_primary"])]
    )
    
    style.map(
        "Treeview.Heading",
        background=[("active", COLORS["accent"])]
    )
    
    # Progressbar
    style.configure(
        "TProgressbar",
        background=COLORS["accent"],
        troughcolor=COLORS["bg_medium"],
        bordercolor=COLORS["border"],
        lightcolor=COLORS["accent"],
        darkcolor=COLORS["accent"]
    )
    
    # Scrollbar
    style.configure(
        "TScrollbar",
        background=COLORS["bg_light"],
        troughcolor=COLORS["bg_dark"],
        bordercolor=COLORS["bg_dark"],
        arrowcolor=COLORS["text_secondary"]
    )
    
    style.map(
        "TScrollbar",
        background=[("active", COLORS["accent"])]
    )


def format_size(size_bytes: int) -> str:
    """Format file size in human-readable format."""
    if size_bytes is None:
        return "-"
    if size_bytes == 0:
        return "0 B"
    
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size_bytes < 1024:
            if unit == 'B':
                return f"{int(size_bytes)} B"
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    
    return f"{size_bytes:.1f} PB"


def format_date(date_str: str) -> str:
    """Format date string for display."""
    if not date_str:
        return "-"
    
    try:
        # Parse ISO format
        from datetime import datetime
        dt = datetime.fromisoformat(date_str)
        return dt.strftime("%Y-%m-%d %H:%M")
    except:
        return date_str
