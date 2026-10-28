#!/usr/bin/env bash
# build-deb.sh — сборка .deb для silero-tts-reader (GTK-версия).
#
# Зависимостей почти нет: нужен dpkg-deb (он есть в Ubuntu/Debian по умолчанию)
# и python3 с PIL для иконок. debhelper НЕ нужен — пакет собирается вручную:
# раскладка файлов + control/md5sums + dpkg-deb --build.
#
# Использование:
#   ./packaging/build-deb.sh                 # версия из git describe (или 2.0.0)
#   VERSION=2.0.0 ./packaging/build-deb.sh   # задать версию явно
#   ./packaging/build-deb.sh --keep          # не удалять build/ после сборки
#
# Результат: dist/silero-tts-reader_<версия>_all.deb
set -euo pipefail

PACKAGE="silero-tts-reader"
MAINTAINER="${MAINTAINER:-Alexey Vostrikov <avostrikov@gmail.com>}"
HOMEPAGE="https://github.com/alexey-vostrikov/silero-tts-reader"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

KEEP=0
[[ "${1:-}" == "--keep" ]] && KEEP=1

# ── Версия ────────────────────────────────────────────────────────────────────
# Формат Debian: цифры и точки; «-» разделяет upstream и ревизию пакета,
# поэтому суффикс git добавляем через «+», а «-» превращаем в «.».
if [[ -n "${VERSION:-}" ]]; then
    DEB_VERSION="$VERSION"
else
    GIT_DESC="$(git describe --tags --always 2>/dev/null || true)"
    if [[ -z "$GIT_DESC" ]]; then
        DEB_VERSION="$(sed -n 's/^version *= *"\(.*\)"/\1/p' pyproject.toml | head -1)"
        DEB_VERSION="${DEB_VERSION:-2.0.0}"
    elif [[ "$GIT_DESC" =~ ^v?[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
        DEB_VERSION="${GIT_DESC#v}"
    elif [[ "$GIT_DESC" =~ ^v?([0-9]+\.[0-9]+\.[0-9]+)-([0-9]+)-g([0-9a-f]+)(-dirty)?$ ]]; then
        DEB_VERSION="${BASH_REMATCH[1]}+${BASH_REMATCH[2]}.g${BASH_REMATCH[3]}${BASH_REMATCH[4]:+.dirty}"
    else
        BASE="$(sed -n 's/^version *= *"\(.*\)"/\1/p' pyproject.toml | head -1)"
        DEB_VERSION="${BASE:-2.0.0}+git${GIT_DESC//-/.}"
    fi
fi
DEB_VERSION="${DEB_VERSION#v}"

echo "=== $PACKAGE $DEB_VERSION (arch: all) ==="

# ── Каталоги ──────────────────────────────────────────────────────────────────
BUILD="$ROOT/build/deb"
PKGROOT="$BUILD/${PACKAGE}_${DEB_VERSION}_all"
DIST="$ROOT/dist"
rm -rf "$PKGROOT"
mkdir -p "$PKGROOT/DEBIAN" "$DIST"

APP_DIR="$PKGROOT/usr/lib/$PACKAGE"

install -d "$APP_DIR" \
    "$PKGROOT/usr/bin" \
    "$PKGROOT/usr/share/applications" \
    "$PKGROOT/usr/share/doc/$PACKAGE" \
    "$PKGROOT/usr/lib/systemd/user"

# ── Код приложения ────────────────────────────────────────────────────────────
cp -a silero_tts_reader "$APP_DIR/"
cp -a run.py "$APP_DIR/"
find "$APP_DIR" -name '__pycache__' -type d -prune -exec rm -rf {} +
find "$APP_DIR" -name '*.py[co]' -delete

# ── Запускатор ────────────────────────────────────────────────────────────────
cat > "$PKGROOT/usr/bin/$PACKAGE" <<'LAUNCHER'
#!/bin/sh
# Запускатор silero-tts-reader: системный python3, код в /usr/lib/silero-tts-reader.
# PyGObject (gi) ставится системным пакетом, поэтому venv здесь не нужен и вреден.
set -e
APP_DIR="/usr/lib/silero-tts-reader"

if [ ! -f "$APP_DIR/run.py" ]; then
    echo "silero-tts-reader: не найден $APP_DIR/run.py — пакет установлен неполностью" >&2
    exit 1
fi

if ! python3 -c "import gi" 2>/dev/null; then
    echo "silero-tts-reader: не установлен PyGObject." >&2
    echo "  sudo apt install python3-gi gir1.2-gtk-3.0" >&2
    exit 1
fi

# Ни torch, ни sounddevice нет в репозиториях Ubuntu — проверяем заранее и
# подсказываем команду, вместо падения трассировкой внутри приложения.
MISSING="$(python3 - <<'PY' 2>/dev/null
import importlib.util as u
mods = [m for m in ("numpy", "sounddevice", "torch") if u.find_spec(m) is None]
print(" ".join(mods))
PY
)"
if [ -n "$MISSING" ]; then
    echo "silero-tts-reader: не хватает Python-модулей: $MISSING" >&2
    echo "  В Ubuntu этих пакетов нет в архиве, ставятся через pip:" >&2
    echo "    sudo apt install python3-pip" >&2
    echo "    python3 -m pip install --user --break-system-packages sounddevice torch" >&2
    exit 1
fi

exec python3 "$APP_DIR/run.py" "$@"
LAUNCHER
chmod 0755 "$PKGROOT/usr/bin/$PACKAGE"

# ── .desktop ──────────────────────────────────────────────────────────────────
sed -e 's|^Exec=.*|Exec=silero-tts-reader|' \
    -e 's|^Icon=.*|Icon=silero-tts-reader|' \
    -e 's|^X-GNOME-Autostart-enabled=.*|X-GNOME-Autostart-enabled=true|' \
    silero-tts-reader.desktop > "$PKGROOT/usr/share/applications/$PACKAGE.desktop"
chmod 0644 "$PKGROOT/usr/share/applications/$PACKAGE.desktop"
# в системный автозапуск пакет не лезет: пользователь решает сам
sed -i '/^X-GNOME-Autostart-enabled=/d' "$PKGROOT/usr/share/applications/$PACKAGE.desktop"

# ── Иконки ────────────────────────────────────────────────────────────────────
# PIL нужен только для иконок: берём первый интерпретатор, у которого он есть
ICON_PY=""
for candidate in python3 /usr/bin/python3; do
    if "$candidate" -c 'import PIL' 2>/dev/null; then ICON_PY="$candidate"; break; fi
done
if [[ -z "$ICON_PY" ]]; then
    echo "не найден PIL (нужен для иконок): установите python3-pil" >&2
    exit 1
fi
"$ICON_PY" packaging/make-icons.py "$PKGROOT/usr/share/icons" >/dev/null
# крупная копия — для .desktop без установленной темы и для диалога «О программе»
install -Dm644 "$PKGROOT/usr/share/icons/silero-tts-reader.png" \
    "$PKGROOT/usr/share/pixmaps/$PACKAGE.png"
rm -f "$PKGROOT/usr/share/icons/silero-tts-reader.png"
find "$PKGROOT/usr/share/icons" -name '*.png' -exec chmod 0644 {} +

# ── systemd user unit (запуск по требованию, не включается автоматически) ─────
sed -e 's|^ExecStart=.*|ExecStart=/usr/bin/silero-tts-reader|' \
    silero-tts-reader.service > "$PKGROOT/usr/lib/systemd/user/$PACKAGE.service"
chmod 0644 "$PKGROOT/usr/lib/systemd/user/$PACKAGE.service"

# ── Документация ──────────────────────────────────────────────────────────────
install -Dm644 LICENSE "$PKGROOT/usr/share/doc/$PACKAGE/copyright"
{
    echo "$PACKAGE ($DEB_VERSION) unstable; urgency=medium"
    echo
    echo "  * Build $(git log -1 --format='%h %s' 2>/dev/null || echo 'from sources')."
    echo
    echo " -- $MAINTAINER  $(date -R)"
} | gzip -9n > "$PKGROOT/usr/share/doc/$PACKAGE/changelog.Debian.gz"
chmod 0644 "$PKGROOT/usr/share/doc/$PACKAGE/changelog.Debian.gz"
gzip -9n < README.md > "$PKGROOT/usr/share/doc/$PACKAGE/README.md.gz"
chmod 0644 "$PKGROOT/usr/share/doc/$PACKAGE/README.md.gz"

# ── control ───────────────────────────────────────────────────────────────────
INSTALLED_SIZE="$(du -sk --exclude=DEBIAN "$PKGROOT" | cut -f1)"

cat > "$PKGROOT/DEBIAN/control" <<CONTROL
Package: $PACKAGE
Version: $DEB_VERSION
Architecture: all
Maintainer: $MAINTAINER
Installed-Size: $INSTALLED_SIZE
Section: sound
Priority: optional
Homepage: $HOMEPAGE
Depends: python3 (>= 3.10), python3-gi, gir1.2-glib-2.0, gir1.2-gtk-3.0, python3-numpy
Recommends: python3-sounddevice, python3-torch, python3-pynput, python3-evdev, gir1.2-ayatanaappindicator3-0.1, gsettings-desktop-schemas, rubberband-cli, xdotool, xclip
Suggests: python3-pyrubberband
Description: Silero TTS reader with global hotkeys and system tray
 Reads the selected text or the clipboard aloud using the Silero TTS v5 model.
 Hotkeys are configurable, transliteration of Latin words can be done with a
 local LLM, and the SILERO v5 model is loaded from ~/.local/share/silero-tts-reader.
 .
 Интерфейс на GTK 3 (PyGObject): плавающий виджет воспроизведения, окно настроек,
 иконка в трее (AppIndicator на Wayland, StatusIcon на X11).
CONTROL

# Права как в системе: файлы root:root, каталоги 0755
chmod 0644 "$PKGROOT/DEBIAN/control"

# ── md5sums и conffiles ───────────────────────────────────────────────────────
( cd "$PKGROOT" && find . -path ./DEBIAN -prune -o -type f -print | sed 's|^\./||' | sort \
    | xargs -r md5sum > DEBIAN/md5sums )
chmod 0644 "$PKGROOT/DEBIAN/md5sums"
: > "$PKGROOT/DEBIAN/conffiles"
chmod 0644 "$PKGROOT/DEBIAN/conffiles"

# ── Сборка ────────────────────────────────────────────────────────────────────
DEB="$DIST/${PACKAGE}_${DEB_VERSION}_all.deb"
rm -f "$DEB"
dpkg-deb --build --root-owner-group -Zxz "$PKGROOT" "$DEB" >/dev/null

[[ "$KEEP" == "1" ]] || rm -rf "$BUILD"

echo
echo "готово: $DEB ($(du -h "$DEB" | cut -f1))"
dpkg-deb --info "$DEB" | sed 's/^/  /'
