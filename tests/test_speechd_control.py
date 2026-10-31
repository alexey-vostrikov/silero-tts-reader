"""Проверки управления модулем speech-dispatcher.

Скрипт пакета лежит без расширения .py, поэтому загружается через
SourceFileLoader: иначе ни импорт, ни ruff его не видят.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import sys
from importlib.machinery import SourceFileLoader
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "packaging" / "speechd" / "silero-tts-speechd"

checks = 0
failures = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global checks, failures
    checks += 1
    if condition:
        print(f"  ✓ {name}{(' — ' + detail) if detail else ''}")
    else:
        failures += 1
        print(f"  ✗ {name}{(' — ' + detail) if detail else ''}")


def load_script():
    loader = SourceFileLoader("silero_tts_speechd", str(SCRIPT))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    if spec is None:
        raise RuntimeError("не удалось собрать спецификацию модуля")
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


print("1. Скрипт на месте и загружается")
check("файл существует", SCRIPT.is_file())
speechd = load_script()
check("модуль загружен", hasattr(speechd, "main"))

print("2. Строка AddModule и маркеры")
check("AddModule про silero-tts", "silero-tts" in speechd.ADD_LINE)
check("маркеры парные", speechd.BEGIN in speechd.BLOCK and speechd.END in speechd.BLOCK)

print("3. Распознавание своих голосов в списке")
mixed = ["NAME LANGUAGE VARIANT", "xenia ru FEMALE1", "en_0 en FEMALE1"]
foreign = ["NAME LANGUAGE VARIANT", "Afrikaans+Adam af Adam",
           "Russian+Adam ru Adam"]
check("свои голоса видны", speechd.has_our_voices(mixed))
check("чужие голоса не за свои", not speechd.has_our_voices(foreign))
check("пустой список", not speechd.has_our_voices([]))

print("4. status замечает неперезапущенный демон")
# Демон, который ещё держит конфиг с момента запуска, показывает голоса
# чужого модуля — это и была жалоба «зачем столько голосов».
source = SCRIPT.read_text(encoding="utf-8")
check("есть подсказка про перезапуск демона",
      "systemctl --user restart speech-dispatcher" in source)
check("status сравнивает голоса", "has_our_voices(voices)" in source)
check("enable перезапускает демон", "restart_daemon()" in source)
check("disable перезапускает демон",
      source.count("restart_daemon()") >= 2,
      f"вызовов {source.count('restart_daemon()')}")

print("5. Перезапуск идёт в сессии того, кто вызвал sudo")
check("учитывается SUDO_USER", 'os.environ.get("SUDO_USER")' in source)
check("сессионная шина возвращается", "XDG_RUNTIME_DIR=" in source)
check("uid считается через id -u", '["id", "-u", user]' in source)

print("6. Разбор списка голосов")
espeak_sample = [
    "NAME                 LANGUAGE                  VARIANT",
    "                  Afrikaans                       af                     none",
    "             Afrikaans+Adam                       af                     Adam",
    "        xenia                       ru                  FEMALE1",
    "  Africa+Half-LifeAnnouncementSystem                       afHalf-LifeAnnouncementSystem",
]
counts = speechd.parse_voices(espeak_sample)
check("языки посчитаны", counts.get("af") == 2, str(counts))
check("свой голос попал", counts.get("ru") == 1, str(counts))
check("слипшаяся строка пропущена, а не принята за язык",
      "afHalf-LifeAnnouncementSystem" not in counts, str(sorted(counts)))
check("заголовок не считается", "LANGUAGE" not in counts, str(sorted(counts)))

print("7. Языки из конфига модуля")
module_conf = (ROOT / "packaging" / "speechd" / "silero-tts.conf").read_text(
    encoding="utf-8")
languages = speechd.module_languages(module_conf)
check("наши языки прочитаны", languages == {"ru", "en"}, str(sorted(languages)))
check("закомментированное не считается",
      speechd.module_languages('#AddVoice "de" "FEMALE1" "x"') == set())

print("8. Диагностика объясняет причину лишних языков")
# Сценарий, который и был у пользователя: в конфиге нет ни одного AddModule,
# демон поднял встроенный espeak-ng и отдал все свои языки.
report, ok = speechd.explain_voices(["espeak-ng"], {"af": 100, "en": 50},
                                    {"ru", "en"}, enabled=False)
text = " ".join(report)
check("найден встроенный espeak-ng", not ok)
check("сказано про отсутствие AddModule", "ни одного AddModule" in text, text)
check("предложено включить модуль", "enable" in text, text)

# Модуль включён, но демон не перезапущен: конфиг новый, состояние старое.
report, ok = speechd.explain_voices(["espeak-ng"], {"af": 100}, {"ru", "en"},
                                    enabled=True)
text = " ".join(report)
check("найден неперезапущенный демон", not ok)
check("сказано про перезапуск", "перезапускался" in text, text)

# Другие модули объявили свои языки.
report, ok = speechd.explain_voices(["silero-tts", "espeak-ng"],
                                    {"ru": 6, "en": 6, "af": 40}, {"ru", "en"},
                                    enabled=True)
text = " ".join(report)
check("перечислены лишние языки", "af" in text and not ok, text)

# Всё в порядке.
report, ok = speechd.explain_voices(["silero-tts"], {"ru": 6, "en": 6},
                                    {"ru", "en"}, enabled=True)
check("чистый список признан нормой", ok, " ".join(report))

# Демон не отвечает.
report, ok = speechd.explain_voices([], {}, {"ru", "en"}, enabled=True)
check("молчащий демон виден", not ok, " ".join(report))

print("9. Клиент вызывается в окружении пользователя")
check("есть помощник окружения", hasattr(speechd, "user_environment"))
source = SCRIPT.read_text(encoding="utf-8")
check("XDG_RUNTIME_DIR подставляется", 'XDG_RUNTIME_DIR"] = f"/run/user/' in source)
check("spd-say вызывается через общий помощник",
      source.count("run_spd_say(") >= 3, f"вызовов {source.count('run_spd_say(')}")

print("10. Установка не добавляет лишнего")
check("в конфиг пишется ровно одна строка AddModule",
      speechd.BLOCK.count("AddModule") == 1, speechd.BLOCK.replace("\n", " | "))
# Скрипт читает конфиг модуля (чтобы знать свои языки), но писать в него не
# должен: голоса и языки остаются в пакете, а не дописываются установкой.
write_body = source.split("def write_conf(", 1)[1].split("\ndef ", 1)[0]
check("запись идёт только в speechd.conf",
      "SPEECHD_CONF" in write_body and "MODULE_CONF" not in write_body,
      "тело write_conf")
check("в конфиг не дописываются голоса",
      "AddVoice" not in write_body and "GenericLanguage" not in write_body)

print(f"\nИТОГ: успешно {checks - failures}, провалено {failures}")
sys.exit(1 if failures else 0)
