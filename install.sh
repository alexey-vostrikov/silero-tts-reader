#!/usr/bin/env bash
# install.sh — установка зависимостей для GTK-версии silero-tts-reader (Debian/Ubuntu, Arch, Fedora)
set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

ok()   { echo -e "${GREEN}✓${NC} $*"; }
warn() { echo -e "${YELLOW}⚠${NC} $*"; }
err()  { echo -e "${RED}✗${NC} $*"; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"

echo "=== Silero TTS Reader (GTK) — установка зависимостей ==="
echo

# ── Определение дистрибутива ──────────────────────────────────────────────────
if command -v pacman &>/dev/null; then
    DISTRO="arch"
elif command -v apt-get &>/dev/null; then
    DISTRO="debian"
elif command -v dnf &>/dev/null; then
    DISTRO="fedora"
else
    DISTRO="unknown"
fi
ok "Дистрибутив: $DISTRO"

case "$DISTRO" in
    arch)   PY="python3" ;;
    *)      PY="python3" ;;
esac

portable() { command -v "$1" &>/dev/null; }

# ── PyGObject + GTK 3: ставится ПАКЕТОМ, а не через pip ───────────────────────
echo
echo "=== GTK 3 и PyGObject (системные пакеты) ==="

if "$PY" -c "import gi; gi.require_version('Gtk','3.0'); from gi.repository import Gtk" &>/dev/null; then
    ok "PyGObject и GTK 3 уже доступны для $PY"
else
    warn "Ставлю python3-gi / gir1.2-gtk-3.0…"
    case "$DISTRO" in
        debian) sudo apt-get update -qq && sudo apt-get install -y python3-gi python3-gi-cairo gir1.2-gtk-3.0 ;;
        arch)   sudo pacman -S --noconfirm python-gobject gtk3 ;;
        fedora) sudo dnf install -y python3-gobject gtk3 ;;
        *)      err "Установите вручную: python3-gi, gir1.2-gtk-3.0"; exit 1 ;;
    esac
    ok "GTK 3 установлен"
fi

# Индикатор в трее для GNOME/Wayland — без него трей недоступен, приложение работает
echo
echo "=== Иконка в трее (AppIndicator) ==="
if "$PY" -c "import gi; gi.require_version('AyatanaAppIndicator3','0.1')" &>/dev/null; then
    ok "AyatanaAppIndicator3 уже доступен"
else
    warn "Без него трей работает только в X11 (Gtk.StatusIcon). На Wayland иконки не будет."
    read -rp "Установить libayatana-appindicator? [Y/n] " answer
    if [[ "${answer,,}" != "n" ]]; then
        case "$DISTRO" in
            debian) sudo apt-get install -y gir1.2-ayatanaappindicator3-0.1 ;;
            arch)   sudo pacman -S --noconfirm libayatana-appindicator ;;
            fedora) sudo dnf install -y libappindicator-gtk3 ;;
        esac
        ok "AppIndicator установлен (в GNOME нужно расширение AppIndicator Support)"
    else
        warn "Пропускаю; трей будет недоступен на Wayland."
    fi
fi

# ── Системные утилиты ─────────────────────────────────────────────────────────
echo
echo "=== Системные инструменты ==="

install_sys_pkg() {
    case "$DISTRO" in
        arch)   sudo pacman -S --noconfirm "$1" ;;
        debian) sudo apt-get install -y "$2" ;;
        fedora) sudo dnf install -y "$3" ;;
        *)      err "Установите вручную: $1"; return 1 ;;
    esac
}

for tool in xdotool xclip; do
    if portable "$tool"; then
        ok "$tool уже установлен"
    else
        warn "Устанавливаю $tool…"
        install_sys_pkg "$tool" "$tool" "$tool"
        ok "$tool установлен"
    fi
done

HAS_RUBBERBAND=false
if portable rubberband; then
    ok "rubberband уже установлен"
    HAS_RUBBERBAND=true
else
    warn "rubberband не найден (изменение скорости без него недоступно)"
    read -rp "Установить rubberband? [y/N] " answer
    if [[ "${answer,,}" == "y" ]]; then
        install_sys_pkg rubberband rubberband-cli rubberband
        ok "rubberband установлен"
        HAS_RUBBERBAND=true
    else
        warn "Пропускаем rubberband."
    fi
fi

# ── Права на устройства ввода (глобальные хоткеи через evdev) ─────────────────
echo
echo "=== Права для глобальных хоткеев (Wayland, evdev) ==="
if [[ ! -f /etc/udev/rules.d/99-input-permissions.rules ]]; then
    echo 'KERNEL=="event*", MODE="0666"' | sudo tee /etc/udev/rules.d/99-input-permissions.rules >/dev/null
    sudo udevadm control --reload-rules && sudo udevadm trigger 2>/dev/null || true
    ok "Правила udev добавлены (/etc/udev/rules.d/99-input-permissions.rules)"
else
    ok "Правила udev уже присутствуют"
fi

# ── Виртуальное окружение ─────────────────────────────────────────────────────
echo
echo "=== Виртуальное окружение ==="

# --system-site-packages обязателен: PyGObject (gi) ставится системным пакетом
# и внутрь изолированного venv не попадает.
if [[ ! -d "$VENV_DIR" ]]; then
    "$PY" -m venv --system-site-packages "$VENV_DIR"
    ok "venv создан с --system-site-packages: $VENV_DIR"
else
    if ! "$VENV_DIR/bin/python" -c "import gi" &>/dev/null; then
        err "Существующий venv не видит gi. Пересоздайте: rm -rf $VENV_DIR && ./install.sh"
        exit 1
    fi
    ok "venv уже существует и видит gi: $VENV_DIR"
fi

PIP="$VENV_DIR/bin/pip"
PYTHON="$VENV_DIR/bin/python"

"$PIP" install --quiet --upgrade pip

echo
echo "=== Python-пакеты (pip → venv) ==="
PIP_PKGS=(numpy torch pynput sounddevice pyperclip python-xlib)
[[ "$DISTRO" == "arch" ]] && PIP_PKGS+=(evdev)
"$PIP" install --quiet "${PIP_PKGS[@]}"
ok "Установлено: ${PIP_PKGS[*]}"

if [[ "$HAS_RUBBERBAND" == "true" ]]; then
    "$PIP" install --quiet pyrubberband
    ok "pyrubberband установлен (изменение скорости активно)"
fi

# ── Модель Silero TTS ────────────────────────────────────────────────────────
echo
echo "=== Модель Silero TTS ==="

MODEL_DIR="$HOME/.local/share/silero-tts-reader"
MODEL_PATH="$MODEL_DIR/v5_ru.pt"
MODEL_URL="https://models.silero.ai/models/tts/ru/v5_ru.pt"

if [[ -f "$MODEL_PATH" ]]; then
    ok "Модель уже существует: $MODEL_PATH"
else
    echo "Скачиваю русскую модель Silero v5 (~145 МБ)…"
    mkdir -p "$MODEL_DIR"
    if portable aria2c; then
        aria2c -x4 -s4 -o v5_ru.pt -d "$MODEL_DIR" "$MODEL_URL"
    elif portable wget; then
        wget --show-progress -O "$MODEL_PATH" "$MODEL_URL"
    elif portable curl; then
        curl -L --progress-bar -o "$MODEL_PATH" "$MODEL_URL"
    else
        err "Нет wget/curl. Скачайте вручную: $MODEL_URL → $MODEL_PATH"
        exit 1
    fi
    ok "Модель скачана: $MODEL_PATH"
fi

CONFIG_FILE="$HOME/.config/silero-tts-reader/config.json"
if [[ -f "$CONFIG_FILE" ]]; then
    "$PYTHON" - "$CONFIG_FILE" "$MODEL_PATH" <<'PY'
import json, sys
cfg_path, model_path = sys.argv[1], sys.argv[2]
cfg = json.load(open(cfg_path, encoding="utf-8"))
cfg.setdefault("tts", {})["model_path"] = model_path
open(cfg_path, "w", encoding="utf-8").write(json.dumps(cfg, ensure_ascii=False, indent=2))
print("Конфиг обновлён: model_path →", model_path)
PY
fi

# ── Проверка ──────────────────────────────────────────────────────────────────
echo
echo "=== Проверка ==="
"$PYTHON" - <<'PY'
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk
import numpy, torch, pynput, sounddevice
print("✓ Все модули доступны")
print(f"  GTK: {Gtk.get_major_version()}.{Gtk.get_minor_version()}.{Gtk.get_micro_version()}")
print(f"  torch: {torch.__version__}")
PY

echo
echo "=== Готово! ==="
ok "Запустить приложение: $PYTHON run.py"
warn "Горячие клавиши работают только на X11 / XWayland (pynput + xdotool)"
