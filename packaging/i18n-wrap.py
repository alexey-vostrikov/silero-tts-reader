#!/usr/bin/env python3
"""Обернуть пользовательские строки интерфейса в _().

Правило узкое и проверяемое: оборачиваются только аргументы-литералы в
известных местах (``label=``, ``help=``, ``set_tooltip_text``, ``_notify`` и
т. п.). Произвольный текст в коде не трогается, поэтому повторный запуск
безопасен, а результат виден в diff.
"""
from __future__ import annotations

import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
PKG = ROOT / "silero_tts_reader"
LOCALE = ROOT / "locale"
DOMAIN = "silero-tts-reader"

CYRILLIC = "[А-Яа-яЁё]"

# (шаблон, замена). \1 — сама строка в кавычках вместе с кавычками.
RULES: list[tuple[str, str]] = [
    # аргументы конструкторов и сеттеров GTK
    # только обычные литералы: f-строки обрабатываются отдельно, вручную
    (rf'(?<![_\w])(label|help|title|text|description)="([^"\n]*{CYRILLIC}[^"\n]*)"(?!\s*\n\s*")',
     r'\1=_("\2")'),
    (rf'set_tooltip_text\(\s*"([^"\n]*{CYRILLIC}[^"\n]*)"\s*\)',
     r'set_tooltip_text(_("\1"))'),
    (rf'set_placeholder_text\(\s*"([^"\n]*{CYRILLIC}[^"\n]*)"\s*\)',
     r'set_placeholder_text(_("\1"))'),
    (rf'set_comments\(\s*"([^"\n]*{CYRILLIC}[^"\n]*)"\s*\)',
     r'set_comments(_("\1"))'),
    # свои вызовы
    (rf'_notify\(\s*"([^"\n]*{CYRILLIC}[^"\n]*)"',
     r'_notify(_("\1")'),
    (rf'add_button\(\s*"([^"\n]*{CYRILLIC}[^"\n]*)"',
     r'add_button(_("\1")'),
    (rf'set_default_response\(\s*([A-Z_]+\s*)\)', r'set_default_response(\1)'),
]

TARGETS = [
    "ui/playback_widget.py",
    "ui/tray_icon.py",
    "ui/settings_window.py",
    "app.py",
    "cli.py",
]


def ensure_import(path: pathlib.Path, relative: str) -> None:
    text = path.read_text(encoding="utf-8")
    line = f"from {relative}i18n import _"
    if line in text:
        return
    match = re.search(r"(?m)^from \.\S+ import .+$", text)
    if match is None:
        # файлы без относительных импортов (один gi) — ставим после gi.repository
        match = re.search(r"(?m)^from gi\.repository import .+$", text)
    if match is None:
        raise SystemExit(f"{path}: некуда вставить {line}")
    text = text[: match.end()] + "\n" + line + text[match.end():]
    path.write_text(text, encoding="utf-8")


def wrap(path: pathlib.Path) -> int:
    text = path.read_text(encoding="utf-8")
    before = text
    for pattern, replacement in RULES:
        text = re.sub(pattern, replacement, text)

    if text != before:
        path.write_text(text, encoding="utf-8")
    changed = sum(1 for a, b in zip(before.splitlines(), text.splitlines()) if a != b)
    return len(before.splitlines()) if before != text else 0


def main() -> int:
    total = 0
    for rel in TARGETS:
        path = PKG / rel
        ensure_import(path, ".." if "/" in rel else ".")
        count = wrap(path)
        print(f"  {rel}: изменён" if count else f"  {rel}: без изменений")
        total += count

    for py in sorted(PKG.rglob("*.py")):
        subprocess.run([sys.executable, "-m", "py_compile", str(py)], check=True)
    print(f"  все модули компилируются ({total} файлов тронуто)")

    LOCALE.mkdir(parents=True, exist_ok=True)
    pot = LOCALE / f"{DOMAIN}.pot"
    subprocess.run(
        [
            "xgettext", "--language=Python", "--from-code=UTF-8",
            f"--output={pot}", f"--package-name={DOMAIN}",
            "--keyword=_", "--keyword=_t", "--add-comments",
            *sorted(str(p) for p in PKG.rglob("*.py")),
        ],
        check=True,
    )
    body = pot.read_text(encoding="utf-8")
    msgids = re.findall(r'(?m)^msgid "((?:[^"\\]|\\.)*)"$', body)
    print(f"  извлечено строк: {len(msgids)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
