#!/usr/bin/env python3
"""
Android Everything - Fast file search for Android devices via ADB.

A Windows desktop application similar to "Everything" that connects to 
Android phones via USB and provides instant file search.

Usage:
    python main.py

Requirements:
    - Python 3.8+
    - ADB (Android Debug Bridge) installed
    - USB debugging enabled on Android device
"""

import logging
import os
import sys

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ui.main_window import MainWindow
from config import APP_DATA_DIR, LOG_PATH


def configure_logging():
    """Configure file logging for both source and windowed executable runs."""
    os.makedirs(APP_DATA_DIR, exist_ok=True)
    file_handler = logging.FileHandler(LOG_PATH, encoding="utf-8")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[file_handler],
    )


def show_startup_error(error: Exception):
    """Display a startup error without depending on a console window."""
    try:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(
            "Android Everything",
            f"Android Everything could not start.\n\n{error}\n\n"
            f"Details were written to:\n{LOG_PATH}",
            parent=root,
        )
        root.destroy()
    except Exception:
        # File logging remains available if Tk itself cannot initialize.
        pass


def main():
    """Application entry point."""
    configure_logging()
    logging.info("Starting Android Everything with Python %s", sys.version)

    try:
        app = MainWindow()
        app.run()
        return 0
    except Exception as error:
        logging.exception("Application startup failed")
        show_startup_error(error)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
