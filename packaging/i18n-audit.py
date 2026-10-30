#!/usr/bin/env python3
"""Аудит локализации: найти видимые пользователю русские строки без _().

Проверяются только места, которые попадают в интерфейс (label/help/tooltip/
заголовки/уведомления). Строки документации и отладочные print в этот список
не входят, но выводятся отдельно, чтобы решение было осознанным.
"""
from __future__ import annotations

import ast
import pathlib
import re
import sys

PKG = pathlib.Path(__file__).resolve().parent.parent / "silero_tts_reader"
CYRILLIC = set("абвгдеёжзийклмнопрстуфхцчшщъыьэюяАБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯ")

UI_CALLS = {
    "label", "help", "title", "text", "description", "tooltip",
    "set_tooltip_text", "set_placeholder_text", "set_comments", "set_title",
    "set_program_name", "set_secondary_text", "format_secondary_text",
    "_notify", "add_button", "set_text", "set_markup", "append_text",
    "_make_button", "_label", "set_hint", "notify",
}

UI_KEYWORDS = {"label", "title", "text", "tooltip", "help", "secondary_text"}


def has_cyrillic(text: str) -> bool:
    return any(ch in CYRILLIC for ch in text)


def _is_service_line(text: str) -> bool:
    """Журнал для отладки: строка начинается с метки в квадратных скобках."""
    return bool(re.match(r"^\[[A-Za-z][\w.]*\]", text))


class Audit(ast.NodeVisitor):
    def __init__(self, path: pathlib.Path) -> None:
        self.path = path
        self.translated = 0
        self.missing: list[tuple[int, str, str]] = []
        self.debug: list[tuple[int, str]] = []
        self._lines = path.read_text(encoding="utf-8").splitlines()

    # строку обернули в _(): учитываем и вызов, и сам литерал
    def visit_Call(self, node: ast.Call) -> None:
        name = ""
        if isinstance(node.func, ast.Name):
            name = node.func.id
        elif isinstance(node.func, ast.Attribute):
            name = node.func.attr

        if name in ("_", "_t", "ngettext"):
            for arg in node.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str) and has_cyrillic(arg.value):
                    self.translated += 1

        if name in UI_CALLS:
            for arg in [*node.args, *node.keywords]:
                value = arg.value if isinstance(arg, ast.keyword) else arg
                self._check(value, f"{name}()")
        elif name in ("print", "Dialog", "Window", "MessageDialog", "Label"):
            for arg in node.args:
                self._check(arg, f"{name}()", debug_only=True)

        self.generic_visit(node)

    def visit_keyword(self, node: ast.keyword) -> None:
        if node.arg in UI_KEYWORDS:
            self._check(node.value, f"{node.arg}=")
        self.generic_visit(node)

    def _check(self, node: ast.AST, where: str, debug_only: bool = False) -> None:
        # уже обёрнуто в _()?
        value_node = node
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ("_", "_t"):
            return
        # f-строки и склейки проверяем по частям
        texts: list[tuple[int, str]] = []
        if isinstance(value_node, ast.Constant) and isinstance(value_node.value, str):
            texts.append((value_node.lineno, value_node.value))
        elif isinstance(value_node, ast.JoinedStr):
            for part in value_node.values:
                if isinstance(part, ast.Constant) and isinstance(part.value, str):
                    texts.append((value_node.lineno, part.value))
        elif isinstance(value_node, ast.BinOp):
            for sub in ast.walk(value_node):
                if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                    texts.append((getattr(sub, "lineno", value_node.lineno), sub.value))

        for lineno, text in texts:
            if not has_cyrillic(text):
                continue
            stripped = text.strip()
            # Служебные строки: журнал с меткой вроде "[TTSEngine]". Они идут в
            # консоль для отладки, а не в интерфейс, и переводить их не нужно.
            if debug_only or _is_service_line(stripped) or self._marked(lineno):
                self.debug.append((lineno, stripped[:70]))
            else:
                self.missing.append((lineno, where, stripped[:70]))

    def _marked(self, lineno: int) -> bool:
        """Строка помечена как осознанно непереводимая (``i18n: нет перевода``)."""
        for number in (lineno, lineno - 1, lineno - 2):
            if number < 1 or number > len(self._lines):
                continue
            if "i18n: нет перевода" in self._lines[number - 1]:
                return True
        return False

    def visit_JoinedStr(self, node: ast.JoinedStr) -> None:
        # f-строка в аргументе-литерале вызова не попала в _check — ловим здесь
        line = node.lineno
        for part in node.values:
            if isinstance(part, ast.Constant) and isinstance(part.value, str) and has_cyrillic(part.value):
                stripped = part.value.strip()
                if _is_service_line(stripped) or self._marked(line):
                    self.debug.append((line, stripped[:70]))
                else:
                    self.missing.append((line, "f-строка", stripped[:70]))
                break
        self.generic_visit(node)


def main() -> int:
    files = sorted(PKG.rglob("*.py"))
    total_missing = 0
    total_translated = 0
    total_debug = 0
    for path in files:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError as exc:
            print(f"{path}: синтаксическая ошибка: {exc}", file=sys.stderr)
            continue
        audit = Audit(path)
        audit.visit(tree)
        total_translated += audit.translated
        total_debug += len(audit.debug)

        # убираем дубли: одно и то же место могло попасть дважды
        seen: set[tuple[int, str]] = set()
        unique = []
        for lineno, where, text in audit.missing:
            key = (lineno, text)
            if key in seen:
                continue
            seen.add(key)
            unique.append((lineno, where, text))

        if unique:
            total_missing += len(unique)
            rel = path.relative_to(PKG.parent)
            print(f"\n{rel}:")
            for lineno, where, text in unique:
                print(f"  {lineno:4} {where:22} {text!r}")

    print(f"\nобёрнуто в _(): {total_translated}")
    print(f"без перевода:    {total_missing}")
    print(f"журнал отладки (перевод не нужен): {total_debug}")
    return 1 if total_missing else 0


if __name__ == "__main__":
    sys.exit(main())
