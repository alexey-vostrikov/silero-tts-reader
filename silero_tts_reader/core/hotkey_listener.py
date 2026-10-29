"""Global hotkey listener — evdev backend (Wayland) with pynput fallback (X11)."""
from __future__ import annotations

import os
import threading
from typing import Callable

# evdev key codes
try:
    from evdev import ecodes as ec
    import evdev
    HAS_EVDEV = True
except ImportError:
    HAS_EVDEV = False

try:
    from pynput import keyboard as pynput_keyboard
    HAS_PYNPUT = True
except ImportError:          # python3-pynput не установлен
    pynput_keyboard = None
    HAS_PYNPUT = False


_MODIFIERS = {
    ec.KEY_LEFTCTRL, ec.KEY_RIGHTCTRL,
    ec.KEY_LEFTALT, ec.KEY_RIGHTALT,
    ec.KEY_LEFTSHIFT, ec.KEY_RIGHTSHIFT,
    ec.KEY_LEFTMETA, ec.KEY_RIGHTMETA,
} if HAS_EVDEV else set()

_REGULAR_KEY_MAP: dict[str, int] = {
    "a": ec.KEY_A, "b": ec.KEY_B, "c": ec.KEY_C, "d": ec.KEY_D,
    "e": ec.KEY_E, "f": ec.KEY_F, "g": ec.KEY_G, "h": ec.KEY_H,
    "i": ec.KEY_I, "j": ec.KEY_J, "k": ec.KEY_K, "l": ec.KEY_L,
    "m": ec.KEY_M, "n": ec.KEY_N, "o": ec.KEY_O, "p": ec.KEY_P,
    "q": ec.KEY_Q, "r": ec.KEY_R, "s": ec.KEY_S, "t": ec.KEY_T,
    "u": ec.KEY_U, "v": ec.KEY_V, "w": ec.KEY_W, "x": ec.KEY_X,
    "y": ec.KEY_Y, "z": ec.KEY_Z,
    "0": ec.KEY_0, "1": ec.KEY_1, "2": ec.KEY_2, "3": ec.KEY_3,
    "4": ec.KEY_4, "5": ec.KEY_5, "6": ec.KEY_6, "7": ec.KEY_7,
    "8": ec.KEY_8, "9": ec.KEY_9,
    "f1": ec.KEY_F1, "f2": ec.KEY_F2, "f3": ec.KEY_F3, "f4": ec.KEY_F4,
    "f5": ec.KEY_F5, "f6": ec.KEY_F6, "f7": ec.KEY_F7, "f8": ec.KEY_F8,
    "f9": ec.KEY_F9, "f10": ec.KEY_F10, "f11": ec.KEY_F11, "f12": ec.KEY_F12,
    "space": ec.KEY_SPACE, "tab": ec.KEY_TAB, "enter": ec.KEY_ENTER, "return": ec.KEY_ENTER,
    "escape": ec.KEY_ESC, "esc": ec.KEY_ESC, "backspace": ec.KEY_BACKSPACE,
    "delete": ec.KEY_DELETE, "del": ec.KEY_DELETE, "insert": ec.KEY_INSERT, "ins": ec.KEY_INSERT,
    "home": ec.KEY_HOME, "end": ec.KEY_END, "page_up": ec.KEY_PAGEUP, "pgup": ec.KEY_PAGEUP,
    "page_down": ec.KEY_PAGEDOWN, "pgdn": ec.KEY_PAGEDOWN,
    "caps_lock": ec.KEY_CAPSLOCK, "num_lock": ec.KEY_NUMLOCK, "pause": ec.KEY_PAUSE,
} if HAS_EVDEV else {}

_PYNPUT_SPECIAL_KEYS = {
    "f1", "f2", "f3", "f4", "f5", "f6", "f7", "f8", "f9", "f10", "f11", "f12",
    "space", "tab", "enter", "return", "esc", "escape", "backspace", "delete",
    "del", "insert", "ins", "home", "end", "page_up", "page_down", "pgup", "pgdn",
    "caps_lock", "num_lock", "print_screen", "prtsc", "pause", "up", "down", "left", "right"
}


_SINGLE_MODIFIER_EVDEV = {
    "ctrl": {ec.KEY_LEFTCTRL, ec.KEY_RIGHTCTRL},
    "alt": {ec.KEY_LEFTALT, ec.KEY_RIGHTALT},
    "shift": {ec.KEY_LEFTSHIFT, ec.KEY_RIGHTSHIFT},
    "super": {ec.KEY_LEFTMETA, ec.KEY_RIGHTMETA},
    "meta": {ec.KEY_LEFTMETA, ec.KEY_RIGHTMETA},
    "cmd": {ec.KEY_LEFTMETA, ec.KEY_RIGHTMETA},
} if HAS_EVDEV else {}


def normalize_for_pynput(hotkey_str: str) -> str:
    """Format hotkey string for pynput.keyboard.GlobalHotKeys.

    - Modifiers and special keys are wrapped in <>: <ctrl>, <alt>, <f9>, <space>
    - Single character keys stay as is: z, r, 5
    """
    parts = [p.strip().strip("<>").lower() for p in hotkey_str.split("+")]
    result = []
    modifiers = {"ctrl", "alt", "shift", "super", "meta", "cmd"}
    for p in parts:
        if p in ("super", "meta"):
            result.append("<cmd>")
        elif p in modifiers or p in _PYNPUT_SPECIAL_KEYS:
            result.append(f"<{p}>")
        else:
            result.append(p)
    return "+".join(result)


def _parse_evdev_hotkey(hotkey_str: str) -> tuple[frozenset[int], frozenset[int]] | None:
    parts = [p.strip().strip("<>").lower() for p in hotkey_str.split("+")]

    mod_name_to_codes = {
        "ctrl": {ec.KEY_LEFTCTRL, ec.KEY_RIGHTCTRL},
        "alt":  {ec.KEY_LEFTALT, ec.KEY_RIGHTALT},
        "shift":{ec.KEY_LEFTSHIFT, ec.KEY_RIGHTSHIFT},
        "super":{ec.KEY_LEFTMETA, ec.KEY_RIGHTMETA},
        "meta": {ec.KEY_LEFTMETA, ec.KEY_RIGHTMETA},
        "cmd":  {ec.KEY_LEFTMETA, ec.KEY_RIGHTMETA},
    }

    if len(parts) == 1 and parts[0] in mod_name_to_codes:
        codes = mod_name_to_codes[parts[0]]
        return frozenset(codes), frozenset(codes)

    mods: set[int] = set()
    trigger: int | None = None

    for part in parts:
        if part in mod_name_to_codes:
            mods.update(mod_name_to_codes[part])
        else:
            code = _REGULAR_KEY_MAP.get(part)
            if code is not None:
                trigger = code

    if trigger is None:
        return None
    return frozenset(mods), frozenset([trigger])


class EvdevHotkeyListener(threading.Thread):
    def __init__(self) -> None:
        super().__init__(daemon=True, name="EvdevHotkeyListener")
        self._hotkeys: dict[tuple[frozenset[int], frozenset[int]], Callable[[], None]] = {}
        self._stop_event = threading.Event()
        self._held_keys: set[int] = set()
        self._dirty_single_modifiers: set[int] = set()
        self._lock = threading.Lock()

    def set_hotkeys(self, hotkey_map: dict[str, Callable[[], None]]) -> None:
        new_hotkeys = {}
        for hk_str, cb in hotkey_map.items():
            parsed = _parse_evdev_hotkey(hk_str)
            if parsed:
                new_hotkeys[parsed] = cb
        with self._lock:
            self._hotkeys = new_hotkeys

    def stop_listening(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        import select
        devices = self.get_keyboards()
        if not devices:
            return

        fds = {d.fd: d for d in devices}

        while not self._stop_event.is_set():
            try:
                r, _, _ = select.select(list(fds.keys()), [], [], 0.2)
            except Exception:
                break

            for fd in r:
                device = fds[fd]
                try:
                    for event in device.read():
                        if event.type == ec.EV_KEY:
                            self._handle_key_event(event.code, event.value)
                except Exception:
                    pass

        for d in devices:
            try:
                d.close()
            except Exception:
                pass

    def _handle_key_event(self, code: int, value: int) -> None:
        with self._lock:
            if value == 1:
                # Mark any existing held modifiers as dirty if a new key is pressed alongside them
                for held_code in list(self._held_keys):
                    if held_code in _MODIFIERS and held_code != code:
                        self._dirty_single_modifiers.add(held_code)
                if code in _MODIFIERS:
                    self._dirty_single_modifiers.discard(code)

                self._held_keys.add(code)
                self._check_combo_hotkeys(code)

            elif value == 0:
                # Check single modifier hotkeys on RELEASE
                if code in _MODIFIERS:
                    if code not in self._dirty_single_modifiers and self._held_keys == {code}:
                        self._check_single_mod_release(code)

                self._held_keys.discard(code)
                if not (self._held_keys & _MODIFIERS):
                    self._dirty_single_modifiers.clear()

    def _check_single_mod_release(self, code: int) -> None:
        for (required_mods, allowed_triggers), callback in self._hotkeys.items():
            if code in allowed_triggers and (allowed_triggers <= _MODIFIERS) and required_mods == allowed_triggers:
                threading.Thread(target=callback, daemon=True).start()

    def _check_combo_hotkeys(self, trigger_code: int) -> None:
        for (required_mods, allowed_triggers), callback in self._hotkeys.items():
            # Skip single modifier hotkeys on press (they trigger on release)
            if allowed_triggers <= _MODIFIERS and required_mods == allowed_triggers:
                continue

            if trigger_code not in allowed_triggers:
                continue

            expected_keys = set(required_mods) | set(allowed_triggers)
            if self._held_keys - expected_keys:
                continue

            held_mods = self._held_keys & _MODIFIERS
            if self._mods_satisfied(required_mods, held_mods):
                def _delayed_check(req_m=required_mods, exp_k=expected_keys, cb=callback):
                    import time
                    time.sleep(0.10)  # 100ms stabilization window
                    with self._lock:
                        if self._held_keys - exp_k:
                            return
                        current_held_mods = self._held_keys & _MODIFIERS
                        if self._mods_satisfied(req_m, current_held_mods):
                            cb()

                threading.Thread(target=_delayed_check, daemon=True).start()

    @staticmethod
    def _mods_satisfied(required: frozenset[int], held: set[int]) -> bool:
        ctrl_req  = bool(required & {ec.KEY_LEFTCTRL,  ec.KEY_RIGHTCTRL})
        alt_req   = bool(required & {ec.KEY_LEFTALT,   ec.KEY_RIGHTALT})
        shift_req = bool(required & {ec.KEY_LEFTSHIFT, ec.KEY_RIGHTSHIFT})
        super_req = bool(required & {ec.KEY_LEFTMETA,  ec.KEY_RIGHTMETA})

        ctrl_held  = bool(held & {ec.KEY_LEFTCTRL,  ec.KEY_RIGHTCTRL})
        alt_held   = bool(held & {ec.KEY_LEFTALT,   ec.KEY_RIGHTALT})
        shift_held = bool(held & {ec.KEY_LEFTSHIFT, ec.KEY_RIGHTSHIFT})
        super_held = bool(held & {ec.KEY_LEFTMETA,  ec.KEY_RIGHTMETA})

        return (
            ctrl_req == ctrl_held and
            alt_req == alt_held and
            shift_req == shift_held and
            super_req == super_held
        )

    @staticmethod
    def get_keyboards() -> list:
        if not HAS_EVDEV:
            return []
        devices = []
        try:
            all_devs = [evdev.InputDevice(p) for p in evdev.list_devices()]
        except (PermissionError, OSError):
            return []

        for dev in all_devs:
            try:
                caps = dev.capabilities()
                if ec.EV_KEY in caps and ec.KEY_A in caps.get(ec.EV_KEY, []):
                    devices.append(dev)
                else:
                    dev.close()
            except Exception:
                pass
        return devices


class PynputHotkeyListener:
    def __init__(self) -> None:
        self._listener: pynput_keyboard.Listener | None = None
        # Список, а не словарь: ключ хоткея содержит dict модификаторов,
        # а dict нехешируем — в словаре он падал с TypeError.
        self._hotkeys: list[tuple[dict[str, bool], str, Callable[[], None]]] = []
        self._held_keys: set[str] = set()
        self._dirty_single_modifiers: set[str] = set()
        self._lock = threading.Lock()

    def set_hotkeys(self, hotkey_map: dict[str, Callable[[], None]]) -> None:
        self.stop_listening()
        new_hotkeys: list[tuple[dict[str, bool], str, Callable[[], None]]] = []
        for hk_str, cb in hotkey_map.items():
            parsed = self._parse_pynput_hotkey(hk_str)
            if parsed:
                req_mods, trigger = parsed
                new_hotkeys.append((req_mods, trigger, cb))

        with self._lock:
            self._hotkeys = new_hotkeys
            try:
                self._listener = pynput_keyboard.Listener(
                    on_press=self._on_press,
                    on_release=self._on_release
                )
                self._listener.daemon = True
                self._listener.start()
            except Exception as e:
                print(f"  ⚠ pynput error: {e}", flush=True)

    @staticmethod
    def _parse_pynput_hotkey(hotkey_str: str) -> tuple[dict[str, bool], str] | None:
        parts = [p.strip().strip("<>").lower() for p in hotkey_str.split("+")]
        req_mods = {"ctrl": False, "alt": False, "shift": False, "super": False}
        trigger = None

        mod_map = {
            "ctrl": "ctrl", "alt": "alt", "shift": "shift",
            "super": "super", "meta": "super", "cmd": "super"
        }

        if len(parts) == 1 and parts[0] in mod_map:
            mod_key = mod_map[parts[0]]
            req_mods[mod_key] = True
            return req_mods, mod_key

        for part in parts:
            if part in mod_map:
                req_mods[mod_map[part]] = True
            else:
                trigger = part

        if not trigger:
            # Пустая строка (или «+» без клавиши) — это не хоткей: раньше
            # возвращался триггер "" и в список попадал нерабочий хоткей.
            return None
        return req_mods, trigger

    def _get_key_name(self, key) -> str:
        if isinstance(key, pynput_keyboard.Key):
            name = key.name
            if name.startswith("ctrl"):
                return "ctrl"
            if name.startswith("alt"):
                return "alt"
            if name.startswith("shift"):
                return "shift"
            if name in ("cmd", "cmd_l", "cmd_r"):
                return "super"
            return name
        elif hasattr(key, "char") and key.char:
            return key.char.lower()
        elif hasattr(key, "vk") and key.vk:
            try:
                return chr(key.vk).lower()
            except Exception:
                return str(key).lower()
        return str(key).lower()

    def _on_press(self, key) -> None:
        key_name = self._get_key_name(key)
        mods = {"ctrl", "alt", "shift", "super"}
        with self._lock:
            for held in list(self._held_keys):
                if held in mods and held != key_name:
                    self._dirty_single_modifiers.add(held)
            if key_name in mods:
                self._dirty_single_modifiers.discard(key_name)

            self._held_keys.add(key_name)
            held_copy = set(self._held_keys)
            hotkeys_copy = list(self._hotkeys)

        for req_mods, trigger, callback in hotkeys_copy:
            is_single_mod = (trigger in mods and req_mods[trigger] and sum(req_mods.values()) == 1)
            if is_single_mod:
                continue

            if key_name == trigger:
                expected_keys = {k for k, v in req_mods.items() if v} | {trigger}
                if held_copy - expected_keys:
                    continue

                held_ctrl = "ctrl" in held_copy
                held_alt = "alt" in held_copy
                held_shift = "shift" in held_copy
                held_super = "super" in held_copy

                if (
                    req_mods["ctrl"] == held_ctrl and
                    req_mods["alt"] == held_alt and
                    req_mods["shift"] == held_shift and
                    req_mods["super"] == held_super
                ):
                    def _delayed_pynput_check(rm=req_mods, tr=trigger, exp_k=expected_keys, cb=callback):
                        import time
                        time.sleep(0.10)  # 100ms stabilization window
                        with self._lock:
                            if self._held_keys - exp_k:
                                return
                            cur_ctrl = "ctrl" in self._held_keys
                            cur_alt = "alt" in self._held_keys
                            cur_shift = "shift" in self._held_keys
                            cur_super = "super" in self._held_keys
                            if (
                                rm["ctrl"] == cur_ctrl and
                                rm["alt"] == cur_alt and
                                rm["shift"] == cur_shift and
                                rm["super"] == cur_super
                            ):
                                cb()

                    threading.Thread(target=_delayed_pynput_check, daemon=True).start()

    def _on_release(self, key) -> None:
        key_name = self._get_key_name(key)
        mods = {"ctrl", "alt", "shift", "super"}
        with self._lock:
            if key_name in mods:
                if key_name not in self._dirty_single_modifiers and self._held_keys == {key_name}:
                    for req_mods, trigger, callback in self._hotkeys:
                        if trigger == key_name and req_mods[key_name] and sum(req_mods.values()) == 1:
                            threading.Thread(target=callback, daemon=True).start()

            self._held_keys.discard(key_name)
            if not (self._held_keys & mods):
                self._dirty_single_modifiers.clear()

    def stop_listening(self) -> None:
        with self._lock:
            if self._listener:
                self._listener.stop()
                self._listener = None


class HotkeyListener:
    """Unified hotkey listener — attempts evdev first (Wayland), falls back to pynput (X11)."""

    def __init__(self) -> None:
        self._evdev_listener: EvdevHotkeyListener | None = None
        self._pynput_listener: PynputHotkeyListener | None = None
        self._backend = "none"

    def set_hotkeys(
        self,
        speak_selection: str,
        speak_clipboard: str,
        stop: str,
        on_speak_selection: Callable[[], None],
        on_speak_clipboard: Callable[[], None],
        on_stop: Callable[[], None],
    ) -> None:
        hotkey_map = {
            speak_selection: on_speak_selection,
            speak_clipboard: on_speak_clipboard,
            stop: on_stop,
        }

        # Check evdev availability
        keyboards = EvdevHotkeyListener.get_keyboards() if HAS_EVDEV else []
        if keyboards:
            self._backend = "evdev"
            print(f"  ✓ Использование evdev (Wayland) — устройств: {len(keyboards)}", flush=True)
            if not self._evdev_listener:
                self._evdev_listener = EvdevHotkeyListener()
            self._evdev_listener.set_hotkeys(hotkey_map)
            if not self._evdev_listener.is_alive():
                self._evdev_listener.start()
        elif not HAS_PYNPUT:
            self._backend = "none"
            print(
                "  ✗ Горячие клавиши недоступны: нет ни evdev (нужны права на /dev/input/), "
                "ни pynput.\n"
                "    sudo apt install python3-pynput   # X11\n"
                "    sudo usermod -aG input $USER      # Wayland, затем перелогиниться",
                flush=True,
            )
            return
        else:
            self._backend = "pynput"
            print("  ⚠ evdev не доступен (требуются права доступа к /dev/input/). Использование pynput (X11 fallback)...", flush=True)
            print("    Для поддежки хоткеев на Wayland выполните: sudo chmod 666 /dev/input/event*", flush=True)
            if not self._pynput_listener:
                self._pynput_listener = PynputHotkeyListener()
            self._pynput_listener.set_hotkeys(hotkey_map)

    def stop_listening(self) -> None:
        if self._evdev_listener:
            self._evdev_listener.stop_listening()
        if self._pynput_listener:
            self._pynput_listener.stop_listening()

    @staticmethod
    def format_hotkey(hotkey_str: str) -> str:
        parts = hotkey_str.split("+")
        result = []
        mapping = {
            "ctrl": "Ctrl", "alt": "Alt", "shift": "Shift",
            "super": "Super", "meta": "Super", "cmd": "Cmd",
        }
        for part in parts:
            part = part.strip().strip("<>")
            result.append(mapping.get(part.lower(), part.upper()))
        return "+".join(result)

    @staticmethod
    def parse_hotkey(human_readable: str) -> str:
        parts = human_readable.split("+")
        result = []
        modifiers = {"ctrl", "alt", "shift", "super", "cmd"}
        for part in parts:
            part = part.strip()
            if part.lower() in modifiers:
                result.append(f"<{part.lower()}>")
            elif part.lower() in _PYNPUT_SPECIAL_KEYS:
                result.append(f"<{part.lower()}>")
            else:
                result.append(part.lower())
        return "+".join(result)
