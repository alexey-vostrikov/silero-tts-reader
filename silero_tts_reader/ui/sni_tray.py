"""Значок в системном лотке через StatusNotifierItem (без GTK).

В GTK4 нет ни ``Gtk.StatusIcon``, ни ``Gtk.Menu``, а единственная привязка
AppIndicator (`gir1.2-ayatanaappindicator3-0.1`) собрана под GTK3 и в
GTK4-процессе не загрузится. Поэтому значок и меню отдаются прямо по D-Bus —
так, как это делают Qt- и Electron-приложения:

* ``org.kde.StatusNotifierItem`` — значок: свойства и сигналы;
* ``org.kde.StatusNotifierWatcher`` — служба, которая значки собирает
  (в GNOME это расширение AppIndicator Support, в KDE — встроенный лоток);
* ``com.canonical.dbusmenu`` — меню по щелчку, его читает та же служба.

D-Bus-имена и объекты не зависят от версии GTK, поэтому модуль годится и для
GTK3, и для GTK4 — при миграции меняется только вызывающий код.
"""
from __future__ import annotations

import os
from collections.abc import Callable

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

WATCHER_NAME = "org.kde.StatusNotifierWatcher"
WATCHER_PATH = "/StatusNotifierWatcher"
ITEM_IFACE = "org.kde.StatusNotifierItem"
DBUSMENU_IFACE = "com.canonical.dbusmenu"

PROPERTIES_IFACE = "org.freedesktop.DBus.Properties"
ITEM_PATH = "/StatusNotifierItem"
MENU_PATH = "/MenuBar"


# Значок, который сейчас показан в этом процессе (см. show()).
_active: StatusNotifierTray | None = None


class MenuEntry:
    """Пункт меню лотка: подпись, действие и (необязательно) разделитель."""

    def __init__(self, label: str = "", action: Callable[[], None] | None = None,
                 separator: bool = False) -> None:
        self.label = label
        self.action = action
        self.separator = separator
        self.children: list[MenuEntry] = []


class StatusNotifierTray:
    """Значок в лотке и меню к нему.

    Все действия приходят из меню лотка, поэтому обработчики передаются
    готовыми: модуль ничего не знает о приложении.
    """

    def __init__(self, icon_name: str, title: str,
                 on_activate: Callable[[], None] | None = None) -> None:
        self._icon_name = icon_name
        self._title = title
        self._on_activate = on_activate
        self._entries: list[MenuEntry] = []
        self._bus: Gio.DBusConnection | None = None
        self._owner_id = 0
        self._menu_id = 0
        # Идентификаторы выгруженных объектов: без них повторный show() после
        # hide() падает — GLib не даёт выгрузить один путь дважды.
        self._registrations: list[int] = []
        self.available = False
        # Имя уникально для процесса: два экземпляра не подерутся за одно имя.
        self._bus_name = f"org.kde.StatusNotifierItem-{os.getpid()}-1"

    # ── Меню ─────────────────────────────────────────────────────────────────

    def set_entries(self, entries: list[MenuEntry]) -> None:
        """Заменить содержимое меню (вызывается после сборки структуры)."""
        self._entries = entries
        self._flatten()
        if self._bus is not None:
            self._emit_layout()

    def _flatten(self) -> None:
        """Разложить дерево в плоский список: dbusmenu спрашивает по id."""
        self._flat: dict[int, MenuEntry] = {}
        self._order: list[tuple[int, int, bool]] = []  # (id, parent, separator)
        next_id = 1

        def walk(entries: list[MenuEntry], parent: int) -> None:
            nonlocal next_id
            for entry in entries:
                entry_id = next_id
                next_id += 1
                self._flat[entry_id] = entry
                self._order.append((entry_id, parent, entry.separator))
                walk(entry.children, entry_id)

        walk(self._entries, 0)
        self._root_children = [entry_id for entry_id, parent, _sep in self._order
                               if parent == 0]

    def _layout_variant(self, parent: int = 0, depth: int = -1) -> GLib.Variant:
        """Структура com.canonical.dbusmenu: (id, свойства, дети).

        Собирать надо через словари и списки, а не через готовые варианты:
        GLib сам строит ``a{sv}`` из словаря, а от готового варианта пытается
        взять ключи и падает с KeyError.
        """

        def props_of(entry_id: int) -> dict:
            return self._entry_props(self._flat[entry_id])

        def node(entry_id: int, level: int) -> GLib.Variant:
            # depth = -1 — всё дерево (так просит большинство служб лотка),
            # 0 — только сам узел, N — N уровней вложенности.
            children = []
            if depth < 0 or level < depth:
                children = [node(child, level + 1) for child, par, _ in self._order
                            if par == entry_id]
            return GLib.Variant("(ia{sv}av)", (entry_id, props_of(entry_id),
                                               children))

        # По спецификации dbusmenu depth = 0 значит «только сам узел»: у корня
        # в этом случае детей нет вовсе, а не «дети без внуков».
        children = []
        if depth != 0:
            children = [node(child, 1) for child, par, _ in self._order
                        if par == parent]
        return GLib.Variant("(ia{sv}av)", (parent, {}, children))

    def _emit_layout(self) -> None:
        if self._bus is None:
            return
        self._bus.emit_signal(None, MENU_PATH, DBUSMENU_IFACE, "LayoutUpdated",
                              GLib.Variant("(ui)", (0, 0)))

    # ── D-Bus ────────────────────────────────────────────────────────────────

    def show(self) -> bool:
        """Зарегистрировать значок. False, если лотка в системе нет.

        Второй значок в том же процессе не создаётся: у приложения один
        объект ``/StatusNotifierItem``, и вторая попытка его выгрузить падает
        на «An object is already exported for the interface».
        """
        global _active
        if _active is not None and _active is not self:
            return False

        try:
            self._bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        except GLib.Error:
            return False

        if not self._registrations:
            node = Gio.DBusNodeInfo.new_for_xml(self._xml())
            for iface in node.interfaces:
                path = MENU_PATH if iface.name == DBUSMENU_IFACE else ITEM_PATH
                self._registrations.append(
                    self._bus.register_object(path, iface, self._on_call, None, None))

        self._owner_id = Gio.bus_own_name_on_connection(
            self._bus, self._bus_name, Gio.BusNameOwnerFlags.NONE, None, None)

        # Служба лотка может быть ещё не запущена — тогда значок просто не
        # покажется, и это не ошибка приложения.
        self.available = self._register_with_watcher()
        if self.available:
            _active = self
        return self.available

    def _register_with_watcher(self) -> bool:
        if self._bus is None:
            return False
        try:
            self._bus.call_sync(
                WATCHER_NAME, WATCHER_PATH, WATCHER_NAME,
                "RegisterStatusNotifierItem",
                # Регистрируемся ИМЕНЕМ службы, а не путём: служба лотка
                # запоминает строку как есть, и по пути она потом не знает,
                # кого спрашивать. При имени объекта берётся /StatusNotifierItem.
                GLib.Variant("(s)", (self._bus_name,)),
                None, Gio.DBusCallFlags.NONE, 3000, None)
        except GLib.Error:
            return False
        return True

    def _xml(self) -> str:
        return f"""
        <node>
          <interface name="{ITEM_IFACE}">
            <property name="Category" type="s" access="read"/>
            <property name="Id" type="s" access="read"/>
            <property name="Title" type="s" access="read"/>
            <property name="Status" type="s" access="read"/>
            <property name="IconName" type="s" access="read"/>
            <property name="IconThemePath" type="s" access="read"/>
            <property name="ItemIsMenu" type="b" access="read"/>
            <property name="Menu" type="o" access="read"/>
            <method name="Activate">
              <arg name="x" type="i" direction="in"/>
              <arg name="y" type="i" direction="in"/>
            </method>
            <method name="SecondaryActivate">
              <arg name="x" type="i" direction="in"/>
              <arg name="y" type="i" direction="in"/>
            </method>
            <method name="ContextMenu">
              <arg name="x" type="i" direction="in"/>
              <arg name="y" type="i" direction="in"/>
            </method>
            <signal name="NewIcon"/>
            <signal name="NewStatus"><arg name="status" type="s"/></signal>
          </interface>
          <interface name="{DBUSMENU_IFACE}">
            <method name="GetLayout">
              <arg name="parentId" type="i" direction="in"/>
              <arg name="recursionDepth" type="i" direction="in"/>
              <arg name="propertyNames" type="as" direction="in"/>
              <arg name="revision" type="u" direction="out"/>
              <arg name="layout" type="(ia{{sv}}av)" direction="out"/>
            </method>
            <method name="GetGroupProperties">
              <arg name="ids" type="ai" direction="in"/>
              <arg name="propertyNames" type="as" direction="in"/>
              <arg name="properties" type="a(ia{{sv}})" direction="out"/>
            </method>
            <method name="GetProperty">
              <arg name="id" type="i" direction="in"/>
              <arg name="name" type="s" direction="in"/>
              <arg name="value" type="v" direction="out"/>
            </method>
            <method name="Event">
              <arg name="id" type="i" direction="in"/>
              <arg name="eventId" type="s" direction="in"/>
              <arg name="data" type="v" direction="in"/>
              <arg name="timestamp" type="u" direction="in"/>
            </method>
            <method name="AboutToShow">
              <arg name="id" type="i" direction="in"/>
              <arg name="needUpdate" type="b" direction="out"/>
            </method>
            <signal name="LayoutUpdated">
              <arg name="revision" type="u"/>
              <arg name="parent" type="i"/>
            </signal>
            <signal name="ItemsPropertiesUpdated">
              <arg name="updatedProps" type="a(ia{{sv}})"/>
              <arg name="removedProps" type="a(ias)"/>
            </signal>
          </interface>
        </node>
        """

    def _on_call(self, _conn, _sender, path, iface, method, params, invocation):
        """Маршрутизация вызовов: у каждого интерфейса свой обработчик.

        Разбор аргументов живёт в обработчиках, а здесь остаётся выбор по
        интерфейсу — иначе одна функция отвечает и за свойства значка, и за
        четыре метода меню, и её нельзя прочитать целиком.
        """
        if iface == PROPERTIES_IFACE:
            self._handle_properties(method, params, invocation)
        elif iface == ITEM_IFACE:
            self._handle_item(method, invocation)
        elif iface == DBUSMENU_IFACE:
            self._handle_menu(method, params, invocation)
        else:
            invocation.return_value(None)

    def _handle_properties(self, method, params, invocation) -> None:
        """org.freedesktop.DBus.Properties: без него лоток не увидит иконку."""
        if method == "GetAll":
            invocation.return_value(GLib.Variant("(a{sv})", (self._properties(),)))
            return
        if method == "Get":
            # Два аргумента: имя интерфейса и имя свойства. Взять первый значит
            # спрашивать у значка про интерфейс и всегда получать пусто.
            value = self.get_property(params.unpack()[1])
            invocation.return_value(GLib.Variant("(v)", (value or GLib.Variant("s", ""),)))
            return
        invocation.return_value(None)

    def _handle_item(self, method, invocation) -> None:
        """Методы значка: щелчок открывает окно приложения."""
        if method == "Activate" and self._on_activate is not None:
            self._on_activate()
        invocation.return_value(None)

    def _handle_menu(self, method, params, invocation) -> None:
        """com.canonical.dbusmenu: раскладка, свойства пунктов и нажатия."""
        if method == "GetLayout":
            self._menu_layout(params, invocation)
        elif method == "GetGroupProperties":
            self._menu_group_properties(params, invocation)
        elif method == "GetProperty":
            self._menu_property(params, invocation)
        elif method == "Event":
            self._menu_event(params, invocation)
        elif method == "AboutToShow":
            invocation.return_value(GLib.Variant("(b)", (False,)))
        else:
            invocation.return_value(None)

    def _menu_layout(self, params, invocation) -> None:
        parent_id, depth = params.unpack()[0], params.unpack()[1]
        # depth = -1 — всё дерево целиком, так просит большинство служб лотка.
        layout = self._layout_variant(parent_id if parent_id else 0, depth)
        # Готовый вариант нельзя вложить в кортеж: GLib попытается построить его
        # заново. Поэтому ответ собирается сборщиком.
        builder = GLib.VariantBuilder.new(GLib.VariantType.new("(u(ia{sv}av))"))
        builder.add_value(GLib.Variant("u", 1))
        builder.add_value(layout)
        invocation.return_value(builder.end())

    def _menu_group_properties(self, params, invocation) -> None:
        ids = params.unpack()[0]
        out = []
        for entry_id in ids or list(self._flat):
            entry = self._flat.get(entry_id)
            if entry is None:
                continue
            out.append((entry_id, self._entry_props(entry)))
        invocation.return_value(GLib.Variant("(a(ia{sv}))", (out,)))

    def _menu_property(self, params, invocation) -> None:
        entry_id, name = params.unpack()[0], params.unpack()[1]
        entry = self._flat.get(entry_id)
        value = GLib.Variant("b", True)
        if entry is not None and name == "label":
            value = GLib.Variant("s", entry.label)
        invocation.return_value(GLib.Variant("(v)", (value,)))

    def _menu_event(self, params, invocation) -> None:
        entry_id, event_id = params.unpack()[0], params.unpack()[1]
        if event_id == "clicked":
            entry = self._flat.get(entry_id)
            if entry is not None and entry.action is not None:
                entry.action()
        invocation.return_value(None)

    @staticmethod
    def _entry_props(entry: MenuEntry) -> dict:
        """Свойства пункта — одни и те же для раскладки и точечного запроса."""
        if entry.separator:
            return {"type": GLib.Variant("s", "separator")}
        return {
            "label": GLib.Variant("s", entry.label),
            "enabled": GLib.Variant("b", True),
            "visible": GLib.Variant("b", True),
        }

    # ── Cвойства значка ──────────────────────────────────────────────────────

    def _properties(self) -> dict[str, GLib.Variant]:
        return {
            "Category": GLib.Variant("s", "ApplicationStatus"),
            "Id": GLib.Variant("s", self._icon_name),
            "Title": GLib.Variant("s", self._title),
            "Status": GLib.Variant("s", "Active"),
            "IconName": GLib.Variant("s", self._icon_name),
            "IconThemePath": GLib.Variant("s", ""),
            "ItemIsMenu": GLib.Variant("b", False),
            "Menu": GLib.Variant("o", MENU_PATH),
        }

    def get_property(self, name: str) -> GLib.Variant | None:
        return self._properties().get(name)

    def hide(self) -> None:
        """Снять значок: освободить имя и выгрузить объекты.

        Объекты выгружаются вместе с именем: иначе следующая попытка показать
        значок упадёт на «An object is already exported for the interface».
        """
        if self._bus is None:
            return
        if self._owner_id:
            Gio.bus_unown_name(self._owner_id)
            self._owner_id = 0
        for registration in self._registrations:
            self._bus.unregister_object(registration)
        self._registrations.clear()
        self.available = False
        global _active
        if _active is self:
            _active = None
