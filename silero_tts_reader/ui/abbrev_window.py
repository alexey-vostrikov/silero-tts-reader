"""Редактор сокращений и аббревиатур.

Зачем отдельное окно. Список встроенных аббревиатур не может быть полным:
«ВДВ» читается по буквам, «ВУЗ» — словом, и решает это не правило, а язык.
Ошибиться молча нельзя: если аббревиатуру прочитать не тем способом, слушатель
услышит не слово, а набор букв. Поэтому решение отдано пользователю, а не
спрятано в данных пакета.

Правки живут в ``~/.config/silero-tts-reader/abbrev.json`` — отдельно от
настроек, чтобы файл можно было переносить и править руками. Встроенные
данные при этом не меняются: выключенная запись попадает в список ``removed``,
и её можно вернуть, просто убрав оттуда строку.
"""

from __future__ import annotations

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")

from gi.repository import Gdk, Gtk  # noqa: E402

from ..core.abbrev import (  # noqa: E402
    Abbreviations,
    load_extra,
    save_extra,
    spell_letters,
)
from ..i18n import _  # noqa: E402
from .css_scope import scope_css  # noqa: E402
from .window_icon import apply_icon  # noqa: E402

# Виды записей: что именно делает сокращение при чтении вслух.
KIND_EXPANSION = "expansion"
KIND_SPELLED = "spelled"
KIND_AS_WORD = "as_word"

def kind_titles() -> tuple[tuple[str, str], ...]:
    """Названия видов записи — по ним строится выпадающий список.

    Функция, а не константа: перевод берётся в момент открытия окна, иначе
    строки застыли бы на языке, который был активен при импорте модуля.
    """
    return (
        (KIND_EXPANSION, _("Расшифровка: «т.д.» → «так далее»")),
        (KIND_SPELLED, _("По буквам: «ВДВ» → «вэ дэ вэ»")),
        (KIND_AS_WORD, _("Как слово: «ВУЗ» читается «вуз», а не по буквам")),
    )


class AbbrevWindow(Gtk.Dialog):
    """Список сокращений с добавлением, выключением и проверкой чтения."""

    def __init__(self, parent, extra_path, builtin_dir=None) -> None:
        super().__init__(transient_for=parent, modal=True)
        self.set_title(_("Сокращения и аббревиатуры"))
        self.set_default_size(720, 560)
        apply_icon(self)
        self._extra_path = extra_path
        self._base_dir = builtin_dir

        # Правки держим в памяти и пишем на диск только по «Сохранить»:
        # иначе «Отмена» была бы обманом.
        self._extra = load_extra(extra_path)

        self.add_button(_("Отмена"), Gtk.ResponseType.CANCEL)
        self.add_button(_("Сохранить"), Gtk.ResponseType.OK)

        self._build_ui()
        self._apply_css()
        self._reload()
        self.connect("response", self._on_response)

    # ── Сборка окна ──────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        box = self.get_content_area()
        box.set_spacing(10)
        box.set_margin_top(12)
        box.set_margin_bottom(12)
        box.set_margin_start(14)
        box.set_margin_end(14)

        hint = Gtk.Label()
        hint.set_xalign(0.0)
        hint.set_wrap(True)
        hint.set_text(_(
            "Аббревиатуру можно прочитать по буквам или как слово, а "
            "сокращение — заменить целой фразой. Выключенная встроенная "
            "запись не удаляется из пакета: её можно вернуть, снова включив."))
        hint.add_css_class("abbrev-hint")
        box.append(hint)

        # ── Добавление ───────────────────────────────────────────────────────
        form = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        box.append(form)

        self._entry_short = Gtk.Entry()
        # i18n: нет перевода — пример-заполнитель, одинаковый на любом языке
        self._entry_short.set_placeholder_text("ВДВ")
        self._entry_short.set_width_chars(12)
        form.append(self._entry_short)

        self._kinds = kind_titles()
        self._kind = Gtk.DropDown.new_from_strings(
            [title for _value, title in self._kinds])
        self._kind.set_selected(1)          # по буквам — самый частый случай
        self._kind.set_tooltip_text(_("Как читать это сокращение"))
        form.append(self._kind)

        self._entry_reading = Gtk.Entry()
        self._entry_reading.set_placeholder_text(
            _("пусто — по буквам; «вэ дэ вэ» — своё чтение"))
        self._entry_reading.set_hexpand(True)
        form.append(self._entry_reading)

        add = Gtk.Button(label=_("Добавить"))
        add.connect("clicked", self._on_add)
        form.append(add)

        # ── Список ───────────────────────────────────────────────────────────
        scroller = Gtk.ScrolledWindow()
        scroller.set_vexpand(True)
        scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        box.append(scroller)

        self._list = Gtk.ListBox()
        self._list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self._list.add_css_class("abbrev-list")
        scroller.set_child(self._list)

        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        box.append(buttons)

        self._toggle = Gtk.Button(label=_("Выключить выбранное"))
        self._toggle.connect("clicked", self._on_toggle)
        buttons.append(self._toggle)

        restore = Gtk.Button(label=_("Вернуть встроенные"))
        restore.set_tooltip_text(_("Убрать все выключения из списка"))
        restore.connect("clicked", self._on_restore)
        buttons.append(restore)

        # ── Проверка ─────────────────────────────────────────────────────────
        check_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        box.append(check_row)
        check_row.append(Gtk.Label(label=_("Проверить:")))

        self._entry_test = Gtk.Entry()
        # i18n: нет перевода — пример-заполнитель, одинаковый на любом языке
        self._entry_test.set_placeholder_text("Служил в ВДВ, см. стр. 5")
        self._entry_test.set_hexpand(True)
        self._entry_test.connect("changed", lambda _e: self._update_preview())
        check_row.append(self._entry_test)

        self._preview = Gtk.Label()
        self._preview.set_xalign(0.0)
        self._preview.set_wrap(True)
        self._preview.add_css_class("abbrev-preview")
        box.append(self._preview)

    def _apply_css(self) -> None:
        provider = Gtk.CssProvider()
        provider.load_from_data(
            scope_css("""
            .abbrev-hint {
                color: alpha(currentColor, 0.72);
                font-size: 12px;
            }
            .abbrev-preview {
                font-size: 13px;
                padding: 6px 8px;
                border-radius: 6px;
                background: alpha(currentColor, 0.07);
            }
            .abbrev-source {
                color: alpha(currentColor, 0.6);
                font-size: 11px;
            }
            row.abbrev-off label {
                color: alpha(currentColor, 0.45);
            }
            .abbrev-window {
                background: none;
            }
            """, "window.abbrev-window").encode()
        )
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )
        self.add_css_class("abbrev-window")

    # ── Данные ───────────────────────────────────────────────────────────────

    def _effective(self) -> list[tuple[str, str, str, bool]]:
        """Что действует сейчас: (вид, сокращение, чтение, своё ли).

        Считается заново из встроенных данных и правок, а не копится в окне:
        иначе после выключения и включения список разъезжался бы с тем, что
        реально применится при чтении.
        """
        base = Abbreviations(
            directory=self._base_dir, extra=self._extra, generic=False)
        data = base._data

        mine_exp = self._extra.get("expansions") or {}
        mine_spelled = self._extra.get("spelled") or {}
        mine_words = {w.upper() for w in (self._extra.get("as_word") or [])}

        rows: list[tuple[str, str, str, bool]] = []
        for short, reading in sorted(data["expansions"].items()):
            rows.append((KIND_EXPANSION, short, reading,
                         short in mine_exp))
        for short, reading in sorted(data["spelled"].items()):
            rows.append((KIND_SPELLED, short.upper(), reading,
                         short in mine_spelled))
        for word in sorted(data["as_word"]):
            rows.append((KIND_AS_WORD, word.upper(),
                         _("читается словом"), word.upper() in mine_words))
        return rows

    def _reload(self) -> None:
        """Перерисовать список: он всегда отражает действующие данные."""
        child = self._list.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self._list.remove(child)
            child = nxt

        for kind, short, reading, mine in self._effective():
            self._list.append(self._make_row(kind, short, reading, mine))

        self._update_preview()

    def _make_row(self, kind: str, short: str, reading: str, mine: bool):
        row = Gtk.ListBoxRow()
        row.kind = kind
        row.short = short
        inner = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        inner.set_margin_top(4)
        inner.set_margin_bottom(4)
        inner.set_margin_start(8)
        inner.set_margin_end(8)

        name = Gtk.Label(label=short)
        name.set_xalign(0.0)
        name.set_width_chars(14)
        inner.append(name)

        arrow = Gtk.Label(label="→")
        arrow.add_css_class("abbrev-source")
        inner.append(arrow)

        value = Gtk.Label(label=reading)
        value.set_xalign(0.0)
        value.set_hexpand(True)
        value.set_ellipsize(3)
        inner.append(value)

        source = Gtk.Label(label=_("своё") if mine else _("встроено"))
        source.add_css_class("abbrev-source")
        inner.append(source)

        row.set_child(inner)
        return row

    # ── Действия ─────────────────────────────────────────────────────────────

    def _on_add(self, _button) -> None:
        short = self._entry_short.get_text().strip()
        reading = self._entry_reading.get_text().strip()
        kind = self._kinds[self._kind.get_selected()][0]
        if not short:
            self._warn(_("Впишите сокращение"))
            return
        if kind == KIND_EXPANSION and not reading:
            self._warn(_("Для расшифровки нужно, чем заменить"))
            return

        if kind == KIND_EXPANSION:
            self._extra["expansions"][short] = reading
        elif kind == KIND_SPELLED:
            self._extra["spelled"][short.upper()] = reading or spell_letters(short)
        else:
            words = self._extra.setdefault("as_word", [])
            if short.upper() not in {w.upper() for w in words}:
                words.append(short.upper())
            # Слово, включённое как читаемое словом, перестаёт быть выключенным.
            self._extra["removed"] = [
                w for w in self._extra.get("removed") or []
                if w.upper() != short.upper()]

        self._extra["removed"] = [
            w for w in self._extra.get("removed") or []
            if w.upper() != short.upper()]
        self._entry_short.set_text("")
        self._entry_reading.set_text("")
        self._reload()

    def _selected_row(self):
        return self._list.get_selected_row()

    def _on_toggle(self, _button) -> None:
        """Выключить свою запись или встроенную — разными способами."""
        row = self._selected_row()
        if row is None:
            self._warn(_("Выберите строку в списке"))
            return

        short = row.short
        removed = self._extra.setdefault("removed", [])
        if short in self._extra["expansions"]:
            del self._extra["expansions"][short]
        elif short.upper() in self._extra["spelled"]:
            del self._extra["spelled"][short.upper()]
        else:
            for key in ("expansions", "spelled"):
                found = [k for k in self._extra[key] if k.upper() == short.upper()]
                for k in found:
                    del self._extra[key][k]
            self._extra["as_word"] = [
                w for w in self._extra["as_word"]
                if w.upper() != short.upper()]
            if short.upper() not in {w.upper() for w in removed}:
                removed.append(short)
        # Выключенное слово не должно оставаться читаемым словом.
        self._extra["as_word"] = [
            w for w in self._extra["as_word"] if w.upper() != short.upper()]
        self._reload()

    def _on_restore(self, _button) -> None:
        self._extra["removed"] = []
        self._reload()

    def _update_preview(self) -> None:
        """Показать, как будет прочитана тестовая строка с текущими правками."""
        text = self._entry_test.get_text().strip()
        if not text:
            self._preview.set_text("")
            return
        expanded = Abbreviations(
            directory=self._base_dir, extra=self._extra, generic=False).expand(text)
        if expanded == text:
            self._preview.set_text(_("Без изменений: {text}").format(text=text))
        else:
            self._preview.set_text(expanded)

    def _warn(self, message: str) -> None:
        dialog = Gtk.MessageDialog(
            transient_for=self, modal=True,
            message_type=Gtk.MessageType.WARNING,
            buttons=Gtk.ButtonsType.OK,
            text=message,
        )
        dialog.connect("response", lambda dlg, _r: dlg.destroy())
        dialog.present()

    def _on_response(self, _dialog, response: int) -> None:
        if response == Gtk.ResponseType.OK:
            save_extra(self._extra_path, self._extra)
        self.destroy()
