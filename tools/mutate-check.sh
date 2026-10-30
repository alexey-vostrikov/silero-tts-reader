#!/bin/bash
# Ручные мутации: каждая обязана дать красный ровно на своём тесте.
#
# По правилу ub-mode «какая правка сделает красным» проверять ПРАВКОЙ, а не
# рассуждением. Восстановление — из копии файла, НЕ через `git checkout`:
# checkout возвращает файл к HEAD и молча сносит незакоммиченную работу.
set -u
cd "$HOME/DevOps/3part/silero-tts-reader" || exit 1

BACKUP_DIR=$(mktemp -d)
trap 'rm -rf "$BACKUP_DIR"' EXIT

# Без байткода: после мутации файл пишется, компилируется в .pyc, а возврат
# из копии даёт ИСХОДНУЮ дату правки — и Python берёт мутированный .pyc как
# свежий. Симптом: файл на диске верный, а поведение — от мутанта.
export PYTHONDONTWRITEBYTECODE=1
find silero_tts_reader tests -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null

run_case() {
    local title="$1" file="$2" from="$3" to="$4" test="$5" env="${6:-plain}"
    printf '  %s\n' "$title"
    if ! grep -qF -- "$from" "$file"; then
        printf '    ОШИБКА: фрагмент не найден в %s (мутация не легла)\n' "$file"
        return 1
    fi

    local backup="$BACKUP_DIR/$(basename "$file")"
    cp -- "$file" "$backup"

    python3 - "$file" "$from" "$to" <<'PY'
import sys, pathlib
path, old, new = sys.argv[1], sys.argv[2], sys.argv[3]
p = pathlib.Path(path)
p.write_text(p.read_text(encoding="utf-8").replace(old, new, 1), encoding="utf-8")
PY

    local out status
    # Окружение теста — параметром: тесту трея нужна своя шина, GTK-тесту экран.
    case "$env" in
        bus) out=$(timeout 240 dbus-run-session -- /usr/bin/python3 "$test" 2>&1) ;;
        gui) out=$(timeout 240 xvfb-run -a /usr/bin/python3 "$test" 2>&1) ;;
        *)   out=$(timeout 240 /usr/bin/python3 "$test" 2>&1) ;;
    esac
    status=$?
    cp -- "$backup" "$file"

    if [ $status -eq 0 ]; then
        printf '    ✗ мутация ВЫЖИЛА — тест зелёный, проверки нет\n'
        return 1
    fi
    printf '    ✓ красный: %s\n' "$(printf '%s' "$out" | grep -E 'AssertionError|TypeError|NameError|UnboundLocal' | head -1 | cut -c1-100)"
    return 0
}

fails=0
run_case "1. rate.py: вернуть перекрытие имени _ (дефект UnboundLocalError)" \
    silero_tts_reader/core/rate.py "stretched, _disk_rate = _read_float_wav(target)" \
    "stretched, _ = _read_float_wav(target)" tests/test_rate.py || fails=$((fails+1))

run_case "2. tts_worker: общее число квантов = номер кванта" \
    silero_tts_reader/core/tts_worker.py "self._on_progress(index, total)" \
    "self._on_progress(index, index)" tests/test_streaming.py || fails=$((fails+1))

run_case "3. Quantum.pause_ms: сумма пауз вместо самой длинной" \
    silero_tts_reader/core/tts_worker.py \
    "return max(0, max((int(value) for value in candidates), default=0))" \
    "return sum(int(value) for value in candidates)" tests/test_streaming.py || fails=$((fails+1))

run_case "4. make_config: снова читать боевой конфиг" \
    tests/test_streaming.py \
    'ConfigManager(path=Path(tempfile.gettempdir()) / "silero-tts-test-none.json")' \
    'ConfigManager()' tests/test_streaming.py || fails=$((fails+1))

run_case "5. emit: не помечать квант после заголовка" \
    silero_tts_reader/core/tts_worker.py 'if kind == "heading":
                pending_after_heading = True' 'if kind == "heading":
                pending_after_heading = False' tests/test_streaming.py || fails=$((fails+1))

run_case "6. трей: Get читает первый аргумент (имя интерфейса)" \
    silero_tts_reader/ui/sni_tray.py "value = self.get_property(params.unpack()[1])" \
    "value = self.get_property(params.unpack()[0])" tests/test_sni_tray.py bus || fails=$((fails+1))

run_case "7. трей: hide() не выгружает объекты" \
    silero_tts_reader/ui/sni_tray.py "            self._bus.unregister_object(registration)" \
    "            pass" tests/test_sni_tray.py bus || fails=$((fails+1))

run_case "8. трей: регистрация путём вместо имени службы" \
    silero_tts_reader/ui/sni_tray.py "GLib.Variant(\"(s)\", (self._bus_name,))," \
    "GLib.Variant(\"(s)\", (ITEM_PATH,))," tests/test_sni_tray.py bus || fails=$((fails+1))

run_case "9. трей: щелчок по пункту не зовёт действие" \
    silero_tts_reader/ui/sni_tray.py "            if entry is not None and entry.action is not None:" \
    "            if entry is not None and entry.action is None:" tests/test_sni_tray.py bus || fails=$((fails+1))

run_case "10. трей: глубина 0 всё равно отдаёт детей" \
    silero_tts_reader/ui/sni_tray.py "        if depth != 0:" \
    "        if depth >= 0:" tests/test_sni_tray.py bus || fails=$((fails+1))

run_case "11. layer_shell: монитор вне диапазона" \
    silero_tts_reader/ui/layer_shell.py \
    "if index < 0 or index >= monitors.get_n_items():" \
    "if index < 0:" tests/test_layer_shell.py || fails=$((fails+1))

run_case "12. GTK4: кнопка скорости всегда активна" \
    silero_tts_reader/ui/playback_widget.py \
    "self._speed_combo.set_sensitive(SPEED_AVAILABLE)" \
    "self._speed_combo.set_sensitive(True)" tests/test_playback.py gui || fails=$((fails+1))

printf '\n  выживших мутаций: %d из 12\n' "$fails"
printf '  git status после прогона:\n'
git status --short | sed 's/^/    /'
exit "$fails"
