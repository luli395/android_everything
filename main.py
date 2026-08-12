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

import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ui.main_window import MainWindow


def main():
    """Application entry point."""
    print("Starting Android Everything...")
    print(f"Python version: {sys.version}")
    print("-" * 40)
    
    try:
        app = MainWindow()
        app.run()
    except Exception as e:
        print(f"Error starting application: {e}")
        import traceback
        traceback.print_exc()
        input("Press Enter to exit...")
        sys.exit(1)


if __name__ == "__main__":
    main()
