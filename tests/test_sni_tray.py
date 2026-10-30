#!/usr/bin/env python3
"""Значок в лотке через StatusNotifierItem — без GTK и без рабочего стола.

Проверяется поведение, которое видит служба лотка: имя на шине, свойства
значка, раскладка меню и то, что нажатия доходят до обработчиков. Служба
лотка подменена заглушкой на отдельной шине — так тест не зависит ни от
GNOME, ни от установленных расширений.

Запуск (нужна своя шина, иначе значение будет в чужой сессии):
  dbus-run-session -- python3 tests/test_sni_tray.py
"""
from __future__ import annotations

import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib

from silero_tts_reader.ui.sni_tray import MenuEntry, StatusNotifierTray

WATCHER = "org.kde.StatusNotifierWatcher"
WATCHER_PATH = "/StatusNotifierWatcher"
ITEM_IFACE = "org.kde.StatusNotifierItem"
MENU_IFACE = "com.canonical.dbusmenu"

CHECKS = 0


def check(condition: bool, message: str) -> None:
    global CHECKS
    CHECKS += 1
    if not condition:
        raise AssertionError(message)
    print(f"  ✓ {message}")


class StubWatcher:
    """Служба лотка-заглушка: запоминает, кто зарегистрировался."""

    WATCHER_XML = f"""
    <node>
      <interface name="{WATCHER}">
        <method name="RegisterStatusNotifierItem">
          <arg name="service" type="s" direction="in"/>
        </method>
        <method name="RegisterStatusNotifierHost">
          <arg name="service" type="s" direction="in"/>
        </method>
        <property name="RegisteredStatusNotifierItems" type="as" access="read"/>
        <property name="IsStatusNotifierHostRegistered" type="b" access="read"/>
        <property name="ProtocolVersion" type="i" access="read"/>
        <signal name="StatusNotifierItemRegistered">
          <arg name="service" type="s"/>
        </signal>
      </interface>
    </node>
    """

    def __init__(self) -> None:
        self.items: list[str] = []
        self._bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        node = Gio.DBusNodeInfo.new_for_xml(self.WATCHER_XML)
        self._bus.register_object(WATCHER_PATH, node.interfaces[0],
                                  self._on_call, None, None)
        self._owner = Gio.bus_own_name_on_connection(
            self._bus, WATCHER, Gio.BusNameOwnerFlags.NONE, None, None)

    def _props(self) -> dict[str, GLib.Variant]:
        return {
            "RegisteredStatusNotifierItems": GLib.Variant("as", self.items),
            "IsStatusNotifierHostRegistered": GLib.Variant("b", True),
            "ProtocolVersion": GLib.Variant("i", 0),
        }

    def _on_call(self, _conn, _sender, _path, iface, method, params, invocation):
        if iface == WATCHER and method == "RegisterStatusNotifierItem":
            name = params.unpack()[0]
            if name not in self.items:
                self.items.append(name)
            invocation.return_value(None)
            return
        if iface == "org.freedesktop.DBus.Properties":
            if method == "GetAll":
                invocation.return_value(GLib.Variant("(a{sv})", (self._props(),)))
            else:
                name = params.unpack()[0]
                invocation.return_value(
                    GLib.Variant("(v)", (self._props()[name],)))
            return
        invocation.return_value(None)

    def stop(self) -> None:
        Gio.bus_unown_name(self._owner)


class BusLoop:
    """Главный цикл шины в отдельном потоке.

    Без него вызов собственного D-Bus-имени из того же потока блокирует шину:
    обработчик не может выполниться, пока поток ждёт ответа.
    """

    def __init__(self) -> None:
        self._loop = GLib.MainLoop()
        self._thread = threading.Thread(target=self._loop.run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._loop.quit()
        self._thread.join(timeout=5)


def call(bus, dest, path, iface, method, args):
    return bus.call_sync(dest, path, iface, method, args, None,
                         Gio.DBusCallFlags.NONE, 3000, None)


def make_tray(hits: list[str]) -> StatusNotifierTray:
    tray = StatusNotifierTray(
        icon_name="silero-tts-reader",
        title="Silero TTS Reader",
        on_activate=lambda: hits.append("activate"),
    )
    tray.set_entries([
        MenuEntry("Показать окно", lambda: hits.append("show")),
        MenuEntry("Открыть файл…", lambda: hits.append("open")),
        MenuEntry(separator=True),
        MenuEntry("Озвучить буфер обмена", lambda: hits.append("clipboard")),
        MenuEntry(separator=True),
        MenuEntry("Выход", lambda: hits.append("quit")),
    ])
    return tray


def test_without_watcher() -> None:
    """Без службы лотка значок не появляется, но приложение не падает."""
    print("\n1. Служба лотка не запущена")
    hits: list[str] = []
    tray = make_tray(hits)
    check(tray.show() is False, "show() честно сообщает, что значка нет")
    check(tray.available is False, "available остаётся False")
    tray.hide()


def test_registration(stub: StubWatcher, bus, loop: BusLoop) -> None:
    print("\n2. Регистрация у службы лотка")
    hits: list[str] = []
    tray = make_tray(hits)
    check(tray.show() is True, "show() сообщает об успехе при живой службе")
    check(stub.items == [tray._bus_name],
          f"служба получила имя нашей службы: {stub.items}")
    check(tray.available is True, "available выставлен в True")
    try:
        test_properties(bus, tray)
        test_menu(bus, tray, hits)
        test_events(bus, tray, hits)
        test_menu_queries(bus, tray)
        test_second_tray_refused(bus)
    finally:
        tray.hide()


def test_properties(bus, tray: StatusNotifierTray) -> None:
    print("\n3. Свойства значка")
    props = call(bus, tray._bus_name, "/StatusNotifierItem",
                 "org.freedesktop.DBus.Properties", "GetAll",
                 GLib.Variant("(s)", (ITEM_IFACE,))).unpack()[0]
    check(props.get("Id") == "silero-tts-reader", "Id совпадает с именем иконки")
    check(props.get("IconName") == "silero-tts-reader",
          "IconName задан — без него лоток рисует пустое место")
    check(props.get("Status") == "Active", "Status = Active")
    check(props.get("Menu") == "/MenuBar", "Menu указывает на объект меню")

    single = call(bus, tray._bus_name, "/StatusNotifierItem",
                  "org.freedesktop.DBus.Properties", "Get",
                  GLib.Variant("(ss)", (ITEM_IFACE, "Title"))).unpack()[0]
    check(single == "Silero TTS Reader", f"Get отдаёт то же значение: {single}")


def menu_labels(bus, tray: StatusNotifierTray) -> tuple[list[str], dict[str, int]]:
    """Развернуть раскладку меню в список подписей и карту «подпись → id»."""
    # GetLayout отдаёт (ревизия, раскладка) — раскладка это второй элемент.
    layout = call(bus, tray._bus_name, "/MenuBar", MENU_IFACE, "GetLayout",
                  GLib.Variant("(iias)", (0, -1, []))).unpack()[1]
    labels: list[str] = []
    ids: dict[str, int] = {}

    def walk(node) -> None:
        node_id, props, children = node
        label = props.get("label")
        if label is not None:
            labels.append(label)
            ids[label] = node_id
        elif props.get("type") == "separator":
            labels.append("---")
        for child in children:
            walk(child)

    walk(layout)
    return labels, ids


def test_menu(bus, tray: StatusNotifierTray, hits: list[str]) -> None:
    print("\n4. Раскладка меню")
    labels, _ = menu_labels(bus, tray)
    check(labels == ["Показать окно", "Открыть файл…", "---",
                     "Озвучить буфер обмена", "---", "Выход"],
          f"пункты идут в заданном порядке, разделители сохранены: {labels}")


def test_events(bus, tray: StatusNotifierTray, hits: list[str]) -> None:
    print("\n5. Нажатия доходят до обработчиков")
    _, ids = menu_labels(bus, tray)

    call(bus, tray._bus_name, "/MenuBar", MENU_IFACE, "Event",
         GLib.Variant("(isvu)", (ids["Показать окно"], "clicked",
                                 GLib.Variant("i", 0), 0)))
    check(hits == ["show"], f"нажатие пункта вызвало обработчик: {hits}")

    call(bus, tray._bus_name, "/StatusNotifierItem", ITEM_IFACE, "Activate",
         GLib.Variant("(ii)", (0, 0)))
    check(hits == ["show", "activate"], f"щелчок по значку: {hits}")

    # Событие не «clicked» (например, наведение) действием быть не должно.
    call(bus, tray._bus_name, "/MenuBar", MENU_IFACE, "Event",
         GLib.Variant("(isvu)", (ids["Выход"], "hovered", GLib.Variant("i", 0), 0)))
    check(hits == ["show", "activate"],
          f"постороннее событие обработчик не зовёт: {hits}")


def test_menu_queries(bus, tray: StatusNotifierTray) -> None:
    """Точечные запросы меню — ими лоток обновляет отдельный пункт."""
    print("\n6. Точечные запросы к меню")
    _, ids = menu_labels(bus, tray)
    label_id = ids["Показать окно"]
    separator_id = next(entry_id for entry_id, entry in tray._flat.items()
                        if entry.separator)

    props = call(bus, tray._bus_name, "/MenuBar", MENU_IFACE, "GetProperty",
                 GLib.Variant("(is)", (label_id, "label"))).unpack()[0]
    check(props == "Показать окно", f"GetProperty(label): {props}")

    other = call(bus, tray._bus_name, "/MenuBar", MENU_IFACE, "GetProperty",
                 GLib.Variant("(is)", (label_id, "toggle-state"))).unpack()[0]
    check(other is True, f"неизвестное свойство — значение по умолчанию: {other}")

    group = call(bus, tray._bus_name, "/MenuBar", MENU_IFACE, "GetGroupProperties",
                 GLib.Variant("(aias)", ([label_id, separator_id, 9999], []))).unpack()[0]
    by_id = dict(group)
    check(by_id[label_id].get("label") == "Показать окно",
          "подпись пункта в групповом ответе")
    check(by_id[separator_id].get("type") == "separator",
          "разделитель помечен типом")
    check(9999 not in by_id, "несуществующий id в ответ не попадает")

    need_update = call(bus, tray._bus_name, "/MenuBar", MENU_IFACE, "AboutToShow",
                       GLib.Variant("(i)", (0,))).unpack()[0]
    check(need_update is False,
          "AboutToShow: обновление не нужно, раскладка всегда свежая")

    shallow = call(bus, tray._bus_name, "/MenuBar", MENU_IFACE, "GetLayout",
                   GLib.Variant("(iias)", (0, 0, []))).unpack()[1]
    _node_id, _props, children = shallow
    check(list(children) == [],
          f"глубина 0 — только корень, без детей: {list(children)}")


def test_second_tray_refused(bus) -> None:
    """Второй значок в том же процессе не создаётся — и не ломает первый."""
    print("\n7. Второй значок в процессе")
    second = StatusNotifierTray(icon_name="silero-tts-reader", title="Второй")
    check(second.show() is False,
          "второй значок получает отказ, а не падение на выгрузке объекта")
    check(second.available is False, "и остаётся в состоянии «не показан»")


def test_icon_switch(bus) -> None:
    """Смена значка меняет свойство и не требует перезапуска панели.

    Панель читает IconName один раз и хранит картинку; без сигнала
    NewIconName значок остаётся прежним, поэтому проверяем и значение
    свойства, и то, что повторная установка того же имени ничего не шлёт.
    """
    print("\n9. Смена значка")
    tray = StatusNotifierTray(icon_name="silero-tts-reader-tray-dark",
                              title="Смена значка")
    tray.set_entries([MenuEntry("Выход", lambda: None)])
    check(tray.show() is True, "значок показан")
    try:
        check(tray.get_property("IconName").unpack() == "silero-tts-reader-tray-dark",
              "исходное имя отдаётся свойством")
        tray.set_icon_name("silero-tts-reader-tray-light-wave")
        check(tray.get_property("IconName").unpack() == "silero-tts-reader-tray-light-wave",
              "новое имя видно панели сразу")
        tray.set_icon_name("")
        check(tray.get_property("IconName").unpack() == "silero-tts-reader-tray-light-wave",
              "пустое имя игнорируется: значок не пропадает")
    finally:
        tray.hide()


def test_activate_without_handler(bus) -> None:
    """Значок без обработчика щелчка не падает на Activate."""
    print("\n8. Щелчок без обработчика")
    tray = StatusNotifierTray(icon_name="silero-tts-reader", title="Без обработчика")
    tray.set_entries([MenuEntry("Выход", lambda: None)])
    check(tray.show() is True, "значок показан")
    try:
        call(bus, tray._bus_name, "/StatusNotifierItem", ITEM_IFACE, "Activate",
             GLib.Variant("(ii)", (0, 0)))
        check(True, "Activate без on_activate проходит молча")
    finally:
        tray.hide()


def main() -> int:
    print("Значок в лотке: StatusNotifierItem")
    loop = BusLoop()
    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    try:
        test_without_watcher()
        stub = StubWatcher()
        try:
            test_registration(stub, bus, loop)
            # Значок без обработчика — после того, как первый снят: в процессе
            # он может быть только один.
            test_icon_switch(bus)
            test_activate_without_handler(bus)
        finally:
            stub.stop()
    finally:
        loop.stop()
    print(f"\nвсе проверки пройдены: {CHECKS}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
