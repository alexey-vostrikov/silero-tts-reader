#!/bin/bash
# Покрытие тестов и CRAP по нему — одной командой.
#
# Зачем: тесты здесь — отдельные скрипты, а не pytest, поэтому авто-раннер
# crapper (`coverage run -m pytest`) их не видит. Каждый скрипт прогоняется
# под coverage, отчёты складываются, затем crapper читает готовый lcov.
#
# Требуется пакет coverage. На хост не ставим — берём готовый venv:
#   python3 -m venv ~/.hermes/cache/scratch/venv-cov
#   ~/.hermes/cache/scratch/venv-cov/bin/pip install coverage
#
# Запуск:
#   COVERAGE_BIN=~/.hermes/cache/scratch/venv-cov/bin/coverage \
#       bash tools/coverage-crap.sh [--threshold 10]
set -u
cd "$(dirname "$0")/.." || exit 1
REPO=$PWD
COVERAGE_BIN=${COVERAGE_BIN:-coverage}
CRAPPER=${CRAPPER:-$HOME/DevOps/3part/crapper/crapper}
OUT=${OUT:-$REPO/target/crap}

if [ ! -x "$COVERAGE_BIN" ] && ! command -v "$COVERAGE_BIN" >/dev/null 2>&1; then
    echo "нет coverage: задайте COVERAGE_BIN" >&2
    exit 1
fi

rm -rf "$OUT"
mkdir -p "$OUT"

# Тесты разложены по требованиям к окружению: без экрана, с экраном и с
# собственной шиной D-Bus. Иначе половина набора молча не выполняется.
HEADLESS=(test_fb2 test_streaming test_rate test_i18n test_instance
          test_layer_shell test_app_actions)
GUI=(test_playback test_multilang smoke_gtk test_window_icon)
BUS=(test_sni_tray)

run_one() {
    local name="$1"; shift
    printf '  %-18s' "$name"
    COVERAGE_FILE="$OUT/.coverage" timeout 600 "$@" >/dev/null 2>&1
    if [ $? -eq 0 ]; then echo "ок"; else echo "ПРОВАЛ"; fi
}

for name in "${HEADLESS[@]}"; do
    run_one "$name" "$COVERAGE_BIN" run --source=silero_tts_reader \
        --parallel-mode "tests/$name.py"
done

if command -v xvfb-run >/dev/null 2>&1; then
    for name in "${GUI[@]}"; do
        run_one "$name" xvfb-run -a "$COVERAGE_BIN" run --source=silero_tts_reader \
            --parallel-mode "tests/$name.py"
    done
else
    echo "  GUI-тесты пропущены: нет xvfb-run"
fi

for name in "${BUS[@]}"; do
    run_one "$name" dbus-run-session -- "$COVERAGE_BIN" run --source=silero_tts_reader \
        --parallel-mode "tests/$name.py"
done

COVERAGE_FILE="$OUT/.coverage" "$COVERAGE_BIN" combine >/dev/null 2>&1
mkdir -p "$OUT/target/coverage/python"
COVERAGE_FILE="$OUT/.coverage" "$COVERAGE_BIN" lcov \
    -o "$OUT/target/coverage/python/lcov.info" >/dev/null 2>&1

if [ ! -s "$OUT/target/coverage/python/lcov.info" ]; then
    echo "  lcov пуст: покрытие не собралось" >&2
    exit 1
fi
printf '  lcov: %s записей\n' "$(grep -c '^SF:' "$OUT/target/coverage/python/lcov.info")"

"$CRAPPER" --use-existing-coverage --root "$OUT" -s "$REPO/silero_tts_reader" "$@" \
    > "$OUT/report.txt" 2>&1
STATUS=$?

echo "  --- сводка ---"
grep -E "^(Median|Worst|Functions|Files)|Score" "$OUT/report.txt" | head -8 | sed 's/^/  /'
echo "  функции с CRAP > 10: $(awk '$NF+0 > 10' "$OUT/report.txt" | wc -l)"
echo "  с нулевым покрытием: $(grep -c "0.0%" "$OUT/report.txt")"
echo "  отчёт: $OUT/report.txt"
exit $STATUS
