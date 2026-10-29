#!/usr/bin/env bash
# build-deb.sh — сборка пакетов Silero TTS (GTK-версия).
#
# Пакетов четыре, раскладка по назначению:
#
#   silero-tts-common                настройки и конфиги, движок синтеза
#                                    (/usr/lib/silero-tts/silero_tts_reader/{config,core})
#   silero-tts-reader                GTK-приложение: /usr/bin/silero-tts-reader,
#                                    .desktop, иконки, user-unit
#   silero-tts-speechd               командная строка: /usr/bin/silero-tts-speak
#                                    и управление модулем silero-tts-speechd
#   speech-dispatcher-silero-tts     конфигурация модуля для speech-dispatcher
#
# Зависимостей почти нет: нужен dpkg-deb (он есть в Ubuntu/Debian по умолчанию)
# и python3 с PIL для иконок. debhelper НЕ нужен — пакеты собираются вручную:
# раскладка файлов + control/md5sums + dpkg-deb --build.
#
# Использование:
#   ./packaging/build-deb.sh                 # версия из git describe (или 2.0.0)
#   VERSION=2.0.0 ./packaging/build-deb.sh   # задать версию явно
#   ./packaging/build-deb.sh --keep          # не удалять build/ после сборки
#
# Результат: dist/<пакет>_<версия>_all.deb × 4
set -euo pipefail

COMMON="silero-tts-common"
READER="silero-tts-reader"
SPEAKER="silero-tts-speechd"
SPD_MODULE="speech-dispatcher-silero-tts"

MAINTAINER="${MAINTAINER:-Alexey Vostrikov <avostrikov@gmail.com>}"
HOMEPAGE="https://github.com/alexey-vostrikov/silero-tts-reader"
APP_ROOT="/usr/lib/silero-tts"       # общий каталог кода для всех пакетов

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

KEEP=0
[[ "${1:-}" == "--keep" ]] && KEEP=1

# ── Версия ────────────────────────────────────────────────────────────────────
# Формат Debian: цифры и точки; «-» разделяет upstream и ревизию пакета,
# поэтому суффикс git добавляем через «+».
# Правила (детерминированные — локальная сборка и CI дают одно и то же):
#   тег на HEAD            v2.1.0          → 2.1.0
#   тег в истории          v2.1.0 + 7 коммитов → 2.1.0+7.gabc1234
#   тегов нет вовсе                        → <version из pyproject>+gitabc1234
if [[ -n "${VERSION:-}" ]]; then
    DEB_VERSION="$VERSION"
else
    BASE_VERSION="$(sed -n 's/^version *= *"\(.*\)"/\1/p' pyproject.toml | head -1)"
    BASE_VERSION="${BASE_VERSION:-2.0.0}"
    SHA="$(git rev-parse --short HEAD 2>/dev/null || true)"
    if [[ -n "$(git status --porcelain 2>/dev/null)" ]]; then
        DIRTY=1
    else
        DIRTY=""
    fi
    EXACT_TAG="$(git describe --tags --exact-match 2>/dev/null || true)"
    LAST_TAG="$(git describe --tags --abbrev=0 2>/dev/null || true)"

    # Тег вида 0.0.1-gtk → 0.0.1+gtk: «-» в версии Debian разделяет upstream и
    # ревизию пакета, поэтому суффикс переносим через «+».
    TAG_RE='^v?([0-9]+\.[0-9]+\.[0-9]+)(-([A-Za-z0-9._]+))?$'

    if [[ "$EXACT_TAG" =~ $TAG_RE ]]; then
        DEB_VERSION="${BASH_REMATCH[1]}"
        [[ -n "${BASH_REMATCH[3]:-}" ]] && DEB_VERSION="${DEB_VERSION}+${BASH_REMATCH[3]}"
    elif [[ "$LAST_TAG" =~ $TAG_RE ]]; then
        COUNT="$(git rev-list --count "${LAST_TAG}..HEAD" 2>/dev/null || echo 0)"
        DEB_VERSION="${BASH_REMATCH[1]}"
        [[ -n "${BASH_REMATCH[3]:-}" ]] && DEB_VERSION="${DEB_VERSION}+${BASH_REMATCH[3]}"
        DEB_VERSION="${DEB_VERSION}+${COUNT}.g${SHA:-unknown}"
    elif [[ -n "$SHA" ]]; then
        DEB_VERSION="${BASE_VERSION}+git${SHA}"
    else
        DEB_VERSION="$BASE_VERSION"   # сборка из архива без git
    fi

    if [[ -n "$DIRTY" && -n "$SHA" ]]; then
        DEB_VERSION="${DEB_VERSION}.dirty"
    fi
fi
DEB_VERSION="${DEB_VERSION#v}"

echo "=== $READER $DEB_VERSION (arch: all, 4 пакета) ==="

# ── Каталоги ──────────────────────────────────────────────────────────────────
BUILD="$ROOT/build/deb"
DIST="$ROOT/dist"
rm -rf "$BUILD"
mkdir -p "$DIST"

root_of() { echo "$BUILD/$1_${DEB_VERSION}_all"; }

# Общие файлы документации: copyright, changelog, README — в каждом пакете
# свои, иначе lintian ругается на пустой /usr/share/doc.
add_docs() {
    local root="$1" name="$2"
    install -d "$root/usr/share/doc/$name"
    install -m644 LICENSE "$root/usr/share/doc/$name/copyright"
    {
        echo "$name ($DEB_VERSION) unstable; urgency=medium"
        echo
        echo "  * Build $(git log -1 --format='%h %s' 2>/dev/null || echo 'from sources')."
        echo
        echo " -- $MAINTAINER  $(date -R)"
    } | gzip -9n > "$root/usr/share/doc/$name/changelog.Debian.gz"
    chmod 0644 "$root/usr/share/doc/$name/changelog.Debian.gz"
}

# Сборка одного пакета: контрольный файл передаётся heredoc-ом целиком.
build_pkg() {
    local name="$1" root="$2" section="${3:-sound}"
    local size
    size="$(du -sk --exclude=DEBIAN "$root" | cut -f1)"
    {
        echo "Package: $name"
        echo "Version: $DEB_VERSION"
        echo "Architecture: all"
        echo "Maintainer: $MAINTAINER"
        echo "Installed-Size: $size"
        echo "Section: $section"
        echo "Priority: optional"
        echo "Homepage: $HOMEPAGE"
        cat
    } > "$root/DEBIAN/control"
    chmod 0644 "$root/DEBIAN/control"

    ( cd "$root" && find . -path ./DEBIAN -prune -o -type f -print | sed 's|^\./||' | sort \
        | xargs -r md5sum > DEBIAN/md5sums )
    chmod 0644 "$root/DEBIAN/md5sums"

    # CONFFILES — путь (внутри пакета) или несколько через пробел; по умолчанию пусто
    : > "$root/DEBIAN/conffiles"
    if [[ -n "${CONFFILES:-}" ]]; then
        printf '%s\n' $CONFFILES > "$root/DEBIAN/conffiles"
    fi
    chmod 0644 "$root/DEBIAN/conffiles"

    local deb="$DIST/${name}_${DEB_VERSION}_all.deb"
    rm -f "$deb"
    dpkg-deb --build --root-owner-group -Zxz "$root" "$deb" >/dev/null
    echo "  собрано: $(basename "$deb") ($(du -h "$deb" | cut -f1))"
}

# ═══ 1. silero-tts-common: настройки, конфиги, движок ═════════════════════════
COMMON_ROOT="$(root_of "$COMMON")"
install -d "$COMMON_ROOT/DEBIAN" \
    "$COMMON_ROOT$APP_ROOT/silero_tts_reader"
cp -a silero_tts_reader/__init__.py silero_tts_reader/config silero_tts_reader/core \
    "$COMMON_ROOT$APP_ROOT/silero_tts_reader/"
find "$COMMON_ROOT$APP_ROOT" -name '__pycache__' -type d -prune -exec rm -rf {} +
find "$COMMON_ROOT$APP_ROOT" -name '*.py[co]' -delete
add_docs "$COMMON_ROOT" "$COMMON"

build_pkg "$COMMON" "$COMMON_ROOT" <<CONTROL
Depends: python3 (>= 3.10), python3-numpy
Recommends: python3-sounddevice, python3-torch, python3-pynput
Suggests: python3-pyrubberband
Description: Silero TTS shared settings, configuration and synthesis engine
 Common part of the Silero TTS packages: the configuration file manager
 (~/.config/silero-tts-reader/config.json), the model path conventions
 (~/.local/share/silero-tts-reader) and the Silero v5 synthesis engine with
 text segmentation. Contains no user interface: the GTK application lives in
 silero-tts-reader, the command line tool in silero-tts-speechd.
CONTROL

# ═══ 2. silero-tts-reader: GTK-приложение ═════════════════════════════════════
READER_ROOT="$(root_of "$READER")"
READER_APP="$READER_ROOT$APP_ROOT"
install -d "$READER_ROOT/DEBIAN" \
    "$READER_APP/silero_tts_reader" \
    "$READER_ROOT/usr/bin" \
    "$READER_ROOT/usr/share/applications" \
    "$READER_ROOT/usr/lib/systemd/user"

cp -a silero_tts_reader/app.py silero_tts_reader/__main__.py silero_tts_reader/ui \
    "$READER_APP/silero_tts_reader/"
cp -a run.py "$READER_APP/"
find "$READER_APP" -name '__pycache__' -type d -prune -exec rm -rf {} +
find "$READER_APP" -name '*.py[co]' -delete

cat > "$READER_ROOT/usr/bin/$READER" <<LAUNCHER
#!/bin/sh
# Запускатор silero-tts-reader: системный python3, код в $APP_ROOT.
# PyGObject (gi) ставится системным пакетом, поэтому venv здесь не нужен и вреден.
set -e
APP_DIR="$APP_ROOT"

if [ ! -f "\$APP_DIR/run.py" ]; then
    echo "silero-tts-reader: не найден \$APP_DIR/run.py — пакет установлен неполностью" >&2
    exit 1
fi

if ! python3 -c "import gi" 2>/dev/null; then
    echo "silero-tts-reader: не установлен PyGObject." >&2
    echo "  sudo apt install python3-gi gir1.2-gtk-3.0" >&2
    exit 1
fi

# torch и sounddevice в Ubuntu 26.04 есть как python3-torch/python3-sounddevice,
# но они в Recommends: проверяем заранее и подсказываем apt, вместо падения
# трассировкой внутри приложения.
MISSING="\$(python3 - <<'PY' 2>/dev/null
import importlib.util as u
mods = [m for m in ("numpy", "sounddevice", "torch") if u.find_spec(m) is None]
print(" ".join(mods))
PY
)"
if [ -n "\$MISSING" ]; then
    echo "silero-tts-reader: не хватает Python-модулей: \$MISSING" >&2
    echo "  Поставьте их из архива Ubuntu:" >&2
    echo "    sudo apt install python3-torch python3-sounddevice" >&2
    exit 1
fi

exec python3 "\$APP_DIR/run.py" "\$@"
LAUNCHER
chmod 0755 "$READER_ROOT/usr/bin/$READER"

# .desktop
sed -e 's|^Exec=.*|Exec=silero-tts-reader|' \
    -e 's|^Icon=.*|Icon=silero-tts-reader|' \
    silero-tts-reader.desktop > "$READER_ROOT/usr/share/applications/$READER.desktop"
chmod 0644 "$READER_ROOT/usr/share/applications/$READER.desktop"
# в системный автозапуск пакет не лезет: пользователь решает сам
sed -i '/^X-GNOME-Autostart-enabled=/d' "$READER_ROOT/usr/share/applications/$READER.desktop"

# Иконки: PIL нужен только для них — берём первый интерпретатор, у которого он есть
ICON_PY=""
for candidate in python3 /usr/bin/python3; do
    if "$candidate" -c 'import PIL' 2>/dev/null; then ICON_PY="$candidate"; break; fi
done
if [[ -z "$ICON_PY" ]]; then
    echo "не найден PIL (нужен для иконок): установите python3-pil" >&2
    exit 1
fi
"$ICON_PY" packaging/make-icons.py "$READER_ROOT/usr/share/icons" >/dev/null
install -Dm644 "$READER_ROOT/usr/share/icons/silero-tts-reader.png" \
    "$READER_ROOT/usr/share/pixmaps/$READER.png"
rm -f "$READER_ROOT/usr/share/icons/silero-tts-reader.png"
find "$READER_ROOT/usr/share/icons" -name '*.png' -exec chmod 0644 {} +

# systemd user unit (запуск по требованию, не включается автоматически)
sed -e 's|^ExecStart=.*|ExecStart=/usr/bin/silero-tts-reader|' \
    silero-tts-reader.service > "$READER_ROOT/usr/lib/systemd/user/$READER.service"
chmod 0644 "$READER_ROOT/usr/lib/systemd/user/$READER.service"

add_docs "$READER_ROOT" "$READER"
gzip -9n < README.md > "$READER_ROOT/usr/share/doc/$READER/README.md.gz"
chmod 0644 "$READER_ROOT/usr/share/doc/$READER/README.md.gz"

build_pkg "$READER" "$READER_ROOT" <<CONTROL
Depends: $COMMON (= $DEB_VERSION), python3 (>= 3.10), python3-gi, gir1.2-glib-2.0, gir1.2-gtk-3.0, gir1.2-ayatanaappindicator3-0.1, rubberband-cli
Recommends: python3-sounddevice, python3-torch, python3-pynput, python3-evdev, gsettings-desktop-schemas, xdotool, xclip
Suggests: python3-pyrubberband
Description: Silero TTS reader with global hotkeys and system tray
 Reads the selected text or the clipboard aloud using the Silero TTS v5 model.
 Hotkeys are configurable, transliteration of Latin words can be done with a
 local LLM, and the SILERO v5 model is loaded from ~/.local/share/silero-tts-reader.
 .
 Интерфейс на GTK 3 (PyGObject): плавающий виджет воспроизведения, окно настроек,
 иконка в трее (AppIndicator на Wayland, StatusIcon на X11).
CONTROL

# ═══ 3. silero-tts-speechd: командная строка для speech-dispatcher ════════════
SPEAKER_ROOT="$(root_of "$SPEAKER")"
install -d "$SPEAKER_ROOT/DEBIAN" \
    "$SPEAKER_ROOT$APP_ROOT/silero_tts_reader" \
    "$SPEAKER_ROOT/usr/bin"
cp -a silero_tts_reader/cli.py "$SPEAKER_ROOT$APP_ROOT/silero_tts_reader/"
find "$SPEAKER_ROOT$APP_ROOT" -name '__pycache__' -type d -prune -exec rm -rf {} +

cat > "$SPEAKER_ROOT/usr/bin/silero-tts-speak" <<SPEAKER_BIN
#!/bin/sh
# Синтез речи из командной строки; вызывается модулем speech-dispatcher.
APP_DIR="$APP_ROOT"
if [ ! -f "\$APP_DIR/silero_tts_reader/cli.py" ]; then
    echo "silero-tts-speak: не найден код в \$APP_DIR (пакет silero-tts-speechd)" >&2
    exit 1
fi
PYTHONPATH="\$APP_DIR\${PYTHONPATH:+:\$PYTHONPATH}" exec python3 -m silero_tts_reader.cli "\$@"
SPEAKER_BIN
chmod 0755 "$SPEAKER_ROOT/usr/bin/silero-tts-speak"

install -m755 packaging/speechd/silero-tts-speechd "$SPEAKER_ROOT/usr/bin/silero-tts-speechd"
add_docs "$SPEAKER_ROOT" "$SPEAKER"
install -m644 packaging/speechd/README.silero-tts-speechd \
    "$SPEAKER_ROOT/usr/share/doc/$SPEAKER/README.Debian"
gzip -9n "$SPEAKER_ROOT/usr/share/doc/$SPEAKER/README.Debian"
chmod 0644 "$SPEAKER_ROOT/usr/share/doc/$SPEAKER/"*

build_pkg "$SPEAKER" "$SPEAKER_ROOT" <<CONTROL
Depends: $COMMON (= $DEB_VERSION), python3 (>= 3.10)
Recommends: python3-sounddevice, python3-torch
Suggests: $SPD_MODULE
Description: Silero TTS command line and speech-dispatcher control tool
 Provides silero-tts-speak, which synthesises text with the Silero v5 model and
 plays it from the command line (or writes a WAV file), and silero-tts-speechd,
 which enables or disables the speech-dispatcher output module described by the
 speech-dispatcher-silero-tts package.
 .
 Как и приложение, требует torch и sounddevice (в Ubuntu 26.04 это пакеты
 python3-torch и python3-sounddevice); запускатор объясняет, чего не хватает,
 вместо падения трассировкой.
CONTROL

# ═══ 4. speech-dispatcher-silero-tts: конфигурация модуля ════════════════════
SPD_ROOT="$(root_of "$SPD_MODULE")"
install -d "$SPD_ROOT/DEBIAN" "$SPD_ROOT/etc/speech-dispatcher/modules"
# conffile: правки пользователя переживут обновление пакета
install -m644 packaging/speechd/silero-tts.conf \
    "$SPD_ROOT/etc/speech-dispatcher/modules/silero-tts.conf"
add_docs "$SPD_ROOT" "$SPD_MODULE"
install -m644 packaging/speechd/README.speech-dispatcher-silero-tts \
    "$SPD_ROOT/usr/share/doc/$SPD_MODULE/README.Debian"
gzip -9n "$SPD_ROOT/usr/share/doc/$SPD_MODULE/README.Debian"
chmod 0644 "$SPD_ROOT/usr/share/doc/$SPD_MODULE/"*

# conffile: правки пользователя переживут обновление пакета
CONFFILES="/etc/speech-dispatcher/modules/silero-tts.conf"

build_pkg "$SPD_MODULE" "$SPD_ROOT" <<CONTROL
Depends: $SPEAKER (= $DEB_VERSION), speech-dispatcher (>= 0.10)
Description: Silero TTS output module configuration for speech-dispatcher
 Adds the Silero TTS v5 voices as an output module of speech-dispatcher, so any
 Speech Dispatcher client (spd-say, Orca, Firefox reader mode and others) can
 speak through the local Silero model.
 .
 Only the module configuration is shipped: the stock sd_generic plugin is used,
 and synthesis is done by silero-tts-speak from the silero-tts-speechd package.
 Enable it with "sudo silero-tts-speechd enable".
CONTROL

[[ "$KEEP" == "1" ]] || rm -rf "$BUILD"

echo
echo "готово, $DIST:"
ls -1 "$DIST"/*.deb | sed 's/^/  /'
