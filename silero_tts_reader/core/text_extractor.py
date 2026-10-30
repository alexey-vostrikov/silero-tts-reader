"""Text extractor — gets selected or clipboard text on both Wayland and X11."""
from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Optional

from ..i18n import _
from . import fb2


class TextExtractor:
    """Extracts text from selection or clipboard across Wayland and X11.

    Methods attempted for selection:
    1. Primary selection via `wl-paste --primary` (Wayland native mouse selection)
    2. Primary selection via `xclip -selection primary` (X11 mouse selection)
    3. Save clipboard -> Send Ctrl+C via `xdotool` / `wtype` -> Read new clipboard -> Restore
    """

    MAX_CHARS = 10_000
    CLIPBOARD_RESTORE_DELAY = 0.15

    def get_selected_text(self) -> Optional[str]:
        """Get currently selected text from active window or primary selection."""
        # 1. Try PRIMARY selection (mouse highlight) first — very reliable on Wayland & X11
        primary = self._get_primary_selection()
        if primary and primary.strip():
            return primary.strip()

        # 2. Try simulated Ctrl+C to copy active selection into clipboard
        saved = self.get_clipboard_text()

        # Send Ctrl+C
        if not self._send_ctrl_c():
            return saved.strip() if (saved and saved.strip()) else None

        time.sleep(0.15)
        new_clip = self.get_clipboard_text()

        # Restore previous clipboard if it changed
        if saved is not None and saved != new_clip:
            self._schedule_clipboard_restore(saved)

        if new_clip and new_clip.strip() and new_clip != saved:
            return new_clip.strip()

        return saved.strip() if (saved and saved.strip()) else None

    def get_clipboard_text(self) -> Optional[str]:
        """Get current textual content of the clipboard (Wayland + X11)."""
        # Try wl-paste first (Wayland)
        try:
            res = subprocess.run(
                ["wl-paste", "--no-newline"],
                capture_output=True,
                text=True,
                timeout=1,
            )
            if res.returncode == 0 and res.stdout:
                return res.stdout
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            pass

        # Try xclip (X11)
        try:
            res = subprocess.run(
                ["xclip", "-selection", "clipboard", "-o"],
                capture_output=True,
                text=True,
                timeout=1,
            )
            if res.returncode == 0 and res.stdout:
                return res.stdout
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            pass

        # Try pyperclip
        try:
            import pyperclip
            val = pyperclip.paste()
            if val:
                return val
        except Exception:
            pass

        return None

    # Расширения, которые имеет смысл читать вслух. Остальное не открываем:
    # случайный бинарник в синтезаторе — это поток мусора в динамике.
    TEXT_SUFFIXES = (".txt", ".md", ".markdown", ".rst", ".log", ".csv", ".json", ".xml")
    # Книги разбираются отдельно: это XML внутри архива, а не просто текст
    BOOK_SUFFIXES = (
        ".fb2", ".fb2.zip", ".fb2.xz", ".fb2.lzma", ".fb2.gz", ".fb2.bz2",
        ".fb2.zst", ".fb2.lz4", ".fb2.tar", ".fb2.tar.gz", ".fb2.tgz",
        ".fb2.tar.xz", ".fb2.txz", ".fb2.tar.bz2", ".fb2.tbz2",
        ".fb2.tar.zst", ".fb2.tzst",
    )

    @classmethod
    def read_text_file(cls, path: str) -> str:
        """Прочитать файл для озвучки: текст или книгу FB2.

        Текст: кодировку не угадываем вслепую — сначала UTF-8 (обычный случай),
        затем cp1251, так открываются старые русские файлы из Windows.
        Книга: контейнер (zip/xz/lzma/gzip/bzip2/zstd/lz4/tar) определяется по
        содержимому, поэтому переименованный файл тоже читается. Пустой файл и
        неподдерживаемое расширение — понятная ошибка, а не тишина.
        """
        file_path = Path(path).expanduser()
        if not file_path.is_file():
            raise FileNotFoundError(_("файл не найден: {path}").format(path=file_path))

        if fb2.is_fb2(file_path):
            return fb2.read(file_path)

        if file_path.suffix.lower() not in cls.TEXT_SUFFIXES:
            raise ValueError(
                _("не текстовый файл: {name} (поддерживаются: {suffixes})").format(
                    name=file_path.name,
                    suffixes=", ".join(cls.TEXT_SUFFIXES + cls.BOOK_SUFFIXES),
                )
            )

        raw = file_path.read_bytes()
        for encoding in ("utf-8", "cp1251"):
            try:
                return raw.decode(encoding)
            except UnicodeDecodeError:
                continue
        return raw.decode("utf-8", errors="replace")

    def _set_clipboard_text(self, text: str) -> None:
        """Write text to clipboard (Wayland + X11)."""
        # Try wl-copy first
        try:
            subprocess.run(
                ["wl-copy"],
                input=text,
                text=True,
                timeout=1,
            )
            return
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            pass

        # Try xclip
        try:
            subprocess.run(
                ["xclip", "-selection", "clipboard"],
                input=text,
                text=True,
                timeout=1,
            )
            return
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            pass

        try:
            import pyperclip
            pyperclip.copy(text)
        except Exception:
            pass

    def _get_primary_selection(self) -> Optional[str]:
        """Read PRIMARY selection (mouse highlight)."""
        # Try wl-paste --primary first (Wayland)
        try:
            res = subprocess.run(
                ["wl-paste", "--primary", "--no-newline"],
                capture_output=True,
                text=True,
                timeout=1,
            )
            if res.returncode == 0 and res.stdout:
                return res.stdout
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            pass

        # Try xclip primary (X11)
        try:
            res = subprocess.run(
                ["xclip", "-selection", "primary", "-o"],
                capture_output=True,
                text=True,
                timeout=1,
            )
            if res.returncode == 0 and res.stdout:
                return res.stdout
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            pass

        return None

    def _send_ctrl_c(self) -> bool:
        """Simulate Ctrl+C keypress."""
        # Try wtype if available (Wayland native)
        try:
            res = subprocess.run(
                ["wtype", "-M", "ctrl", "c", "-m", "ctrl"],
                timeout=1,
            )
            if res.returncode == 0:
                return True
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            pass

        # Try ydotool if available
        try:
            res = subprocess.run(
                ["ydotool", "key", "29:1", "46:1", "46:0", "29:0"],
                timeout=1,
            )
            if res.returncode == 0:
                return True
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            pass

        # Try xdotool (X11 / XWayland)
        try:
            res = subprocess.run(
                ["xdotool", "key", "--clearmodifiers", "ctrl+c"],
                timeout=1,
            )
            if res.returncode == 0:
                return True
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            pass

        return False

    def _schedule_clipboard_restore(self, text: str) -> None:
        import threading
        def _restore():
            time.sleep(self.CLIPBOARD_RESTORE_DELAY)
            self._set_clipboard_text(text)
        threading.Thread(target=_restore, daemon=True, name="ClipboardRestore").start()
