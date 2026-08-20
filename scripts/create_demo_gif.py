"""Create the privacy-safe animated demo used by the project README.

The script renders the real Tkinter interface with an in-memory demo backend,
so no Android device, ADB process, local index, or private phone data is used.

This developer utility requires Pillow and a visible Windows desktop session:

    python -m pip install Pillow
    python scripts/create_demo_gif.py
"""

from __future__ import annotations

import argparse
import math
import sys
import time
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

try:
    from PIL import Image, ImageDraw, ImageFont, ImageGrab
except ImportError as error:  # pragma: no cover - developer environment check
    raise SystemExit(
        "Pillow is required. Install it with: python -m pip install Pillow"
    ) from error


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from adb_wrapper import DeviceInfo  # noqa: E402
import ui.main_window as main_window_module  # noqa: E402


SOURCE_SIZE = (1200, 800)
DEFAULT_OUTPUT = PROJECT_ROOT / "docs" / "assets" / "android-everything-demo.gif"
DEMO_SERIAL = "DEMO-PIXEL-8"

DEMO_FILES = [
    {
        "name": "photo_sunset.jpg",
        "path": "/storage/emulated/0/DCIM/Camera/photo_sunset.jpg",
        "size": 4_821_337,
        "modified": "2026-08-18T19:42:00",
        "extension": ".jpg",
        "is_dir": False,
    },
    {
        "name": "photo_family.png",
        "path": "/storage/emulated/0/Pictures/photo_family.png",
        "size": 2_146_752,
        "modified": "2026-08-16T10:18:00",
        "extension": ".png",
        "is_dir": False,
    },
    {
        "name": "photo_report.pdf",
        "path": "/storage/emulated/0/Download/photo_report.pdf",
        "size": 683_212,
        "modified": "2026-08-12T08:31:00",
        "extension": ".pdf",
        "is_dir": False,
    },
    {
        "name": "holiday_clip.mp4",
        "path": "/storage/emulated/0/DCIM/Camera/holiday_clip.mp4",
        "size": 37_192_811,
        "modified": "2026-08-11T21:02:00",
        "extension": ".mp4",
        "is_dir": False,
    },
    {
        "name": "meeting_notes.txt",
        "path": "/storage/emulated/0/Documents/meeting_notes.txt",
        "size": 12_904,
        "modified": "2026-08-10T15:47:00",
        "extension": ".txt",
        "is_dir": False,
    },
]


class DemoSearchEngine:
    """Small in-memory backend that mirrors the methods used by MainWindow."""

    def __init__(self) -> None:
        self.db = object()
        self.indexed = False

    def get_file_count(self, device_serial: str) -> int:
        return len(DEMO_FILES) if self.indexed else 0

    def get_extension_stats(self, device_serial: str) -> List[Tuple[str, int]]:
        return [
            (".jpg", 1),
            (".png", 1),
            (".pdf", 1),
            (".mp4", 1),
            (".txt", 1),
        ]

    def clear_cache(self) -> None:
        return None

    def search(
        self,
        device_serial: str,
        query: str,
        extension_filter: Optional[str] = None,
    ) -> List[dict]:
        normalized_query = query.casefold().strip()
        results = [
            item
            for item in DEMO_FILES
            if not normalized_query
            or normalized_query in item["name"].casefold()
            or normalized_query in item["path"].casefold()
        ]
        if extension_filter:
            results = [
                item for item in results if item["extension"] == extension_filter
            ]
        return results


class DemoADB:
    """Expose one obviously synthetic connected device."""

    def get_devices(self) -> List[DeviceInfo]:
        return [
            DeviceInfo(
                serial=DEMO_SERIAL,
                state="device",
                model="Pixel Demo",
            )
        ]


class DemoIndexer:
    """Placeholder used while the script drives progress states explicitly."""

    def __init__(self, adb: DemoADB, db: object) -> None:
        self.is_indexing = False

    def cancel(self) -> None:
        return None


def _pump_events(root, seconds: float = 0.08) -> None:
    """Let Tk finish layout and painting without entering mainloop."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        root.update()
        time.sleep(0.01)
    root.update_idletasks()
    root.update()


def _capture_client(window) -> Image.Image:
    """Capture only the 1200 x 800 application client area."""
    _pump_events(window.root)
    x = window.root.winfo_rootx()
    y = window.root.winfo_rooty()
    width = window.root.winfo_width()
    height = window.root.winfo_height()
    if (width, height) != SOURCE_SIZE:
        raise RuntimeError(
            f"Unexpected client size {width}x{height}; expected "
            f"{SOURCE_SIZE[0]}x{SOURCE_SIZE[1]}"
        )
    return ImageGrab.grab(
        bbox=(x, y, x + width, y + height),
        include_layered_windows=True,
    ).convert("RGB")


def _load_font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    filename = "segoeuib.ttf" if bold else "segoeui.ttf"
    font_path = Path("C:/Windows/Fonts") / filename
    try:
        return ImageFont.truetype(str(font_path), size=size)
    except OSError:
        return ImageFont.load_default()


def _paint_context_menu(base: Image.Image) -> Image.Image:
    """Paint the app's dark context menu over a captured selected row.

    Native Windows menus enter their own blocking event loop while visible,
    which makes deterministic same-process capture unreliable. Drawing the
    four real actions here keeps the animation reproducible and pixel-stable.
    """
    frame = base.convert("RGBA")
    overlay = Image.new("RGBA", frame.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    x, y, width = 700, 142, 230
    row_height = 37
    menu_height = row_height * 4 + 11
    draw.rounded_rectangle(
        (x + 5, y + 6, x + width + 5, y + menu_height + 6),
        radius=5,
        fill=(0, 0, 0, 105),
    )
    draw.rectangle(
        (x, y, x + width, y + menu_height),
        fill="#16213e",
        outline="#606080",
        width=2,
    )
    draw.rectangle(
        (x + 2, y + 2, x + width - 2, y + row_height),
        fill="#e94560",
    )
    menu_font = _load_font(17)
    items = ["Pull to PC", "Show in Explorer", "Copy Path", "Delete"]
    row_tops = [y, y + row_height, y + row_height * 2, y + row_height * 3 + 11]
    for label, row_top in zip(items, row_tops):
        draw.text(
            (x + 18, row_top + 8),
            label,
            font=menu_font,
            fill="white",
        )
    separator_y = y + row_height * 3 + 5
    draw.line(
        (x + 8, separator_y, x + width - 8, separator_y),
        fill="#606080",
        width=1,
    )
    return Image.alpha_composite(frame, overlay).convert("RGB")


def _draw_cursor(draw: ImageDraw.ImageDraw, position: Tuple[float, float]) -> None:
    """Draw a high-contrast mouse pointer without recording the real cursor."""
    x, y = position
    points = [
        (x, y),
        (x + 2, y + 29),
        (x + 9, y + 22),
        (x + 15, y + 35),
        (x + 21, y + 32),
        (x + 15, y + 19),
        (x + 26, y + 18),
    ]
    draw.polygon(points, fill="#ffffff", outline="#090b16")
    draw.line(points + [points[0]], fill="#090b16", width=2, joint="curve")


def _decorate_frame(
    base: Image.Image,
    cursor: Tuple[float, float],
    step: int,
    caption: str,
    output_size: Tuple[int, int],
    ripple: float | None = None,
) -> Image.Image:
    """Add a step card, synthetic cursor, and optional click ripple."""
    frame = base.convert("RGBA")
    overlay = Image.new("RGBA", frame.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    card_x, card_y, card_w, card_h = 26, 660, 520, 62
    draw.rounded_rectangle(
        (card_x, card_y, card_x + card_w, card_y + card_h),
        radius=15,
        fill=(9, 12, 28, 225),
        outline=(233, 69, 96, 180),
        width=2,
    )
    draw.rounded_rectangle(
        (card_x + 12, card_y + 11, card_x + 82, card_y + 51),
        radius=11,
        fill=(233, 69, 96, 255),
    )
    step_font = _load_font(18, bold=True)
    caption_font = _load_font(20, bold=True)
    draw.text(
        (card_x + 27, card_y + 19),
        f"{step}/5",
        font=step_font,
        fill="white",
    )
    draw.text(
        (card_x + 98, card_y + 17),
        caption,
        font=caption_font,
        fill="white",
    )

    if ripple is not None:
        radius = 8 + 24 * ripple
        alpha = int(230 * (1.0 - ripple))
        click_x, click_y = cursor
        draw.ellipse(
            (
                click_x - radius,
                click_y - radius,
                click_x + radius,
                click_y + radius,
            ),
            outline=(255, 107, 138, alpha),
            width=4,
        )

    _draw_cursor(draw, cursor)
    frame = Image.alpha_composite(frame, overlay).convert("RGB")
    return frame.resize(output_size, Image.Resampling.LANCZOS)


def _smooth_move(
    start: Tuple[float, float],
    end: Tuple[float, float],
    frame_count: int,
) -> Iterable[Tuple[float, float]]:
    """Yield cursor coordinates using smoothstep easing."""
    for index in range(frame_count):
        amount = (index + 1) / frame_count
        eased = amount * amount * (3.0 - 2.0 * amount)
        yield (
            start[0] + (end[0] - start[0]) * eased,
            start[1] + (end[1] - start[1]) * eased,
        )


def _build_base_screens() -> Tuple[Dict[str, Image.Image], object]:
    """Render every meaningful UI state once with the real widgets."""
    engine = DemoSearchEngine()
    main_window_module.get_search_engine = lambda: engine
    main_window_module.get_adb = lambda: DemoADB()
    main_window_module.FileIndexer = DemoIndexer

    window = main_window_module.MainWindow()
    window.root.geometry(f"{SOURCE_SIZE[0]}x{SOURCE_SIZE[1]}+40+40")
    window.root.attributes("-topmost", True)
    window.root.lift()
    window.root.focus_force()
    _pump_events(window.root, 0.22)

    screens: Dict[str, Image.Image] = {}
    screens["connected"] = _capture_client(window)

    window._begin_device_operation()
    window.index_btn.configure(text="Stop")
    window.progress.grid()
    for key, message, value in [
        ("index_1", "Scanning /storage/emulated/0...", 18),
        ("index_2", "Reading file metadata...", 55),
        ("index_3", "Committing index atomically...", 92),
    ]:
        window._update_progress(message, value, 100)
        screens[key] = _capture_client(window)

    engine.indexed = True
    window._on_indexing_complete(DEMO_SERIAL, len(DEMO_FILES))
    screens["indexed"] = _capture_client(window)

    window.search_entry.focus_force()
    window._search_has_focus = True
    window.search_var.set("")
    window._do_search()
    screens["search_empty"] = _capture_client(window)

    for query in ("p", "ph", "pho", "phot", "photo"):
        window.search_var.set(query)
        window._do_search()
        screens[f"query_{query}"] = _capture_client(window)

    window.root.tk.call("ttk::combobox::Post", window.ext_combo)
    screens["filter_open"] = _capture_client(window)
    window.root.tk.call("ttk::combobox::Unpost", window.ext_combo)

    window.ext_var.set("JPG")
    window._do_search()
    window.count_var.set("1 file")
    screens["filtered"] = _capture_client(window)

    first_item = window.file_list.tree.get_children()[0]
    window.file_list.tree.selection_set(first_item)
    window.file_list.tree.focus(first_item)
    screens["selected"] = _capture_client(window)

    screens["menu"] = _paint_context_menu(screens["selected"])
    return screens, window


def _build_animation(
    screens: Dict[str, Image.Image],
    output_size: Tuple[int, int],
) -> List[Image.Image]:
    """Assemble a roughly 14-second, five-step demo at 10 FPS."""
    frames: List[Image.Image] = []

    device = (835.0, 51.0)
    index = (1115.0, 51.0)
    search = (440.0, 51.0)
    filter_box = (635.0, 51.0)
    result_row = (250.0, 151.0)
    menu_action = (890.0, 158.0)

    def add_hold(
        screen: str,
        cursor: Tuple[float, float],
        step: int,
        caption: str,
        count: int,
    ) -> None:
        for _ in range(count):
            frames.append(
                _decorate_frame(
                    screens[screen], cursor, step, caption, output_size
                )
            )

    def add_move(
        screen: str,
        start: Tuple[float, float],
        end: Tuple[float, float],
        step: int,
        caption: str,
        count: int,
    ) -> None:
        for cursor in _smooth_move(start, end, count):
            frames.append(
                _decorate_frame(
                    screens[screen], cursor, step, caption, output_size
                )
            )

    def add_click(
        screen: str,
        cursor: Tuple[float, float],
        step: int,
        caption: str,
    ) -> None:
        for ripple in (0.0, 0.33, 0.66, 1.0):
            frames.append(
                _decorate_frame(
                    screens[screen],
                    cursor,
                    step,
                    caption,
                    output_size,
                    ripple=ripple,
                )
            )

    add_hold("connected", device, 1, "Connect and select your device", 10)
    add_move("connected", device, index, 2, "Build a reusable local index", 8)
    add_click("connected", index, 2, "Build a reusable local index")
    for screen in ("index_1", "index_2", "index_3"):
        add_hold(screen, index, 2, "Build a reusable local index", 6)
    add_hold("indexed", index, 2, "Build a reusable local index", 8)

    add_move("indexed", index, search, 3, "Type to search instantly", 10)
    add_click("indexed", search, 3, "Type to search instantly")
    add_hold("search_empty", search, 3, "Type to search instantly", 2)
    for query in ("p", "ph", "pho", "phot", "photo"):
        add_hold(f"query_{query}", search, 3, "Type to search instantly", 4)
    add_hold("query_photo", search, 3, "Type to search instantly", 6)

    add_move("query_photo", search, filter_box, 4, "Filter results by file type", 8)
    add_click("query_photo", filter_box, 4, "Filter results by file type")
    add_hold("filter_open", filter_box, 4, "Filter results by file type", 5)
    add_hold("filtered", filter_box, 4, "Filter results by file type", 5)

    add_move("filtered", filter_box, result_row, 5, "Pull a result to your PC", 10)
    add_click("filtered", result_row, 5, "Pull a result to your PC")
    add_hold("selected", result_row, 5, "Pull a result to your PC", 5)
    add_click("selected", (700.0, 152.0), 5, "Pull a result to your PC")
    add_move("menu", (700.0, 152.0), menu_action, 5, "Pull a result to your PC", 6)
    add_hold("menu", menu_action, 5, "Pull a result to your PC", 14)
    return frames


def _save_optimized_gif(
    frames: Sequence[Image.Image],
    output: Path,
    frame_duration_ms: int,
) -> None:
    """Use a shared 128-color palette for stable colors and a compact file."""
    output.parent.mkdir(parents=True, exist_ok=True)
    palette_seed = frames[0].convert(
        "P",
        palette=Image.Palette.ADAPTIVE,
        colors=128,
        dither=Image.Dither.NONE,
    )
    palette_frames = [palette_seed]
    palette_frames.extend(
        frame.quantize(palette=palette_seed, dither=Image.Dither.NONE)
        for frame in frames[1:]
    )
    palette_frames[0].save(
        output,
        save_all=True,
        append_images=palette_frames[1:],
        duration=frame_duration_ms,
        loop=0,
        optimize=True,
        disposal=1,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create the privacy-safe Android Everything README demo GIF."
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"Output path (default: {DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--width",
        type=int,
        default=960,
        help="Output width in pixels; the 3:2 aspect ratio is preserved.",
    )
    parser.add_argument(
        "--fps",
        type=int,
        default=10,
        choices=range(5, 21),
        metavar="5-20",
        help="Animation frame rate (default: 10).",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if sys.platform != "win32":
        raise SystemExit("This capture script requires a Windows desktop session.")
    if args.width < 480:
        raise SystemExit("--width must be at least 480 pixels.")

    height = math.floor(args.width * SOURCE_SIZE[1] / SOURCE_SIZE[0])
    output_size = (args.width, height)
    screens, window = _build_base_screens()
    try:
        frames = _build_animation(screens, output_size)
        _save_optimized_gif(
            frames,
            args.output.resolve(),
            frame_duration_ms=round(1000 / args.fps),
        )
    finally:
        window.context_menu.unpost()
        window.root.destroy()

    size_mib = args.output.resolve().stat().st_size / (1024 * 1024)
    duration = len(frames) / args.fps
    print(
        f"Created {args.output.resolve()} "
        f"({output_size[0]}x{output_size[1]}, {len(frames)} frames, "
        f"{duration:.1f}s, {size_mib:.2f} MiB)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
