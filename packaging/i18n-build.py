#!/usr/bin/env python3
"""Собрать каталог en_US из .pot и словаря переводов.

Каталог ru_RU не нужен: исходные строки в коде уже русские, и gettext
возвращает сам msgid. Скрипт заодно проверяет, что переведена КАЖДАЯ строка —
непереведённая упала бы в русский текст внутри английского интерфейса.
"""
from __future__ import annotations

import pathlib
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOCALE = ROOT / "locale"
DOMAIN = "silero-tts-reader"
POT = LOCALE / f"{DOMAIN}.pot"
PO_DIR = LOCALE / "en_US" / "LC_MESSAGES"
PO = PO_DIR / f"{DOMAIN}.po"
MO = PO_DIR / f"{DOMAIN}.mo"

TRANSLATIONS = {
    "  ⚠ Иконка в трее недоступна: в системе нет службы значков\n    (в GNOME это расширение AppIndicator Support). Сообщения идут\n    уведомлениями, управление — из окна и с горячих клавиш.": "  ⚠ Tray icon unavailable: no icon service in the system\n    (in GNOME it is the AppIndicator Support extension). Messages go\n    as notifications; control from the window and hotkeys.",
    # правки после перевода на GTK4
    '\\r  {file}: {percent:3d}%  ({done} из {total} МБ)': '\\r  {file}: {percent:3d}%  ({done} of {total} MB)',
    'После заголовка главы:': 'After the chapter heading:',
    'Отделяет название главы от её текста': 'Separates the chapter title from its body',
    'Тишина на границе абзацев внутри главы': 'Silence at paragraph boundaries inside a chapter',
    "  ⚠ Иконка в трее недоступна: нужен пакет gir1.2-ayatanaappindicator3-0.1\n"
    "    и расширение AppIndicator в GNOME Shell. Сообщения идут уведомлениями.":
        "  ⚠ Tray icon unavailable: package gir1.2-ayatanaappindicator3-0.1 is required\n"
        "    plus the AppIndicator extension in GNOME Shell. Messages go to notifications.",

    "\n✓ Silero TTS Reader запущен": "\n✓ Silero TTS Reader started",
    "\n  Горячие клавиши:": "\n  Hotkeys:",
    "\n    Озвучить выделенное : {hotkey}": "\n    Speak selection : {hotkey}",
    "\n    Озвучить буфер      : {hotkey}": "\n    Speak clipboard : {hotkey}",
    "\n    Стоп                : {hotkey}": "\n    Stop            : {hotkey}",
    "\n  Иконка в трее — правая кнопка мыши для меню":
        "\n  Tray icon — right click for the menu",
    "\n  Нажмите Ctrl+C в терминале для выхода\n":
        "\n  Press Ctrl+C in the terminal to quit\n",

    "Текст не выделен": "No text selected",
    "Модель TTS ещё загружается, подождите...": "The TTS model is still loading, please wait…",
    "Буфер обмена не содержит текст": "The clipboard contains no text",
    "Привет! Это голос {speaker} со скоростью {speed}x.":
        "Hello! This is the voice {speaker} at speed {speed}x.",
    "Не удалось синтезировать речь": "Could not synthesise speech",
    "Длинный текст": "Long text",
    "Отмена": "Cancel",
    "Обрезать до лимита": "Trim to the limit",
    "Озвучить полностью": "Speak everything",
    "Текст содержит {count} символов (лимит: {limit}).\nЧто сделать?":
        "The text has {count} characters (limit: {limit}).\nWhat should be done?",
    "Озвучивание выделенного текста с помощью Silero TTS.\n"
    "Горячие клавиши и голос настраиваются в Настройках.":
        "Reads the selected text aloud with Silero TTS.\n"
        "Hotkeys and voice are configured in Settings.",

    "Синтез речи Silero TTS из командной строки (для speech-dispatcher).":
        "Silero TTS speech synthesis from the command line (for speech-dispatcher).",
    "текст; «-» или пусто — читать stdin": "text; “-” or empty — read stdin",
    "голос (xenia, aidar, en_0...)": "voice (xenia, aidar, en_0…)",
    "скорость речи 0.5–3.0 (1.0 — как есть)": "speech speed 0.5–3.0 (1.0 — as is)",
    "записать WAV, не воспроизводить": "write a WAV file instead of playing",
    "путь к русской модели (переопределяет конфиг)":
        "path to the Russian model (overrides the config)",
    "показать голоса и выйти": "list voices and exit",

    "🔊 Воспроизведение": "🔊 Playback",
    "⏸ Пауза": "⏸ Paused",
    "Нажмите для перемотки": "Click to seek",
    "Скорость:": "Speed:",
    "Скорость воспроизведения": "Playback speed",
    "(rubberband не найден)": "(rubberband not found)",
    "Назад на 5 секунд": "Back 5 seconds",
    "Пауза / Продолжить": "Pause / Resume",
    "Вперёд на 5 секунд": "Forward 5 seconds",
    "Остановить": "Stop",

    "Настройки — Silero TTS Reader": "Settings — Silero TTS Reader",
    "Сброс к умолчаниям": "Reset to defaults",
    "Сохранить": "Save",
    "ГЛОБАЛЬНЫЕ ГОРЯЧИЕ КЛАВИШИ": "GLOBAL HOTKEYS",
    "ГОЛОС": "VOICE",
    "▶  Прослушать": "▶  Preview",
    "СКОРОСТЬ ВОСПРОИЗВЕДЕНИЯ ПО УМОЛЧАНИЮ": "DEFAULT PLAYBACK SPEED",
    "(требуется rubberband-cli)": "(rubberband-cli required)",
    "ОФОРМЛЕНИЕ И ТЕМА ИНТЕРФЕЙСА": "APPEARANCE AND INTERFACE THEME",
    "Использовать системный акцентный цвет": "Use the system accent colour",
    "ТРАНСЛИТЕРАЦИЯ": "TRANSLITERATION",
    "Игнорировать ссылки (http/https/www)": "Ignore links (http/https/www)",
    "Переписывать латинские/иностранные слова кириллицей":
        "Rewrite Latin/foreign words in Cyrillic",
    "Использовать ИИ (LLM) для фонетической транскрипции":
        "Use AI (LLM) for phonetic transcription",
    "НАСТРОЙКИ ИИ-МОДЕЛИ (API)": "AI MODEL SETTINGS (API)",
    "Системный промпт ИИ с правилом расстановки знака ударения '+'":
        "AI system prompt with the stress mark '+' rule",
    "Системный промпт ИИ:": "AI system prompt:",
    "🧪 Проверить подключение ИИ": "🧪 Test the AI connection",
    "Тест ИИ API": "AI API test",
    "Ошибка ИИ API": "AI API error",
    "Сбросить все настройки к значениям по умолчанию?":
        "Reset all settings to their defaults?",


    # поля окна настроек
    "Озвучить выделенное:": "Speak selection:",
    "Озвучить буфер обмена:": "Speak clipboard:",
    "Остановить:": "Stop:",
    "Тема приложения:": "Application theme:",
    "Значок в трее:": "Tray icon:",
    "Авто (по системной теме)": "Auto (follow system theme)",
    "Светлый штрих (для тёмной панели)": "Light strokes (for dark panel)",
    "Тёмный штрих (для светлой панели)": "Dark strokes (for light panel)",
    "Провайдер:": "Provider:",
    "API Base URL:": "API base URL:",
    "API Key:": "API key:",
    "Название модели:": "Model name:",
    "Авто (следовать системной теме)": "Auto (follow the system theme)",
    "Тёмная тема": "Dark theme",
    "Светлая тема": "Light theme",
    "Ollama (Локальный)": "Ollama (local)",
    "OpenAI (Облачный)": "OpenAI (cloud)",
    "Пользовательский (Custom API)": "Custom (custom API)",

    # язык интерфейса
    "Язык интерфейса: {language}": "Interface language: {language}",
    "Авто (из системы)": "Auto (from the system)",
    "Язык интерфейса:": "Interface language:",
    "Язык интерфейса изменится после перезапуска приложения.":
        "The interface language will change after the application restarts.",
    "Язык интерфейса": "Interface language",

    # сборка и один экземпляр
    "Сборка: {build}": "Build: {build}",
    "Заменяю работающую сборку {old} на {new}":
        "Replacing the running build {old} with {new}",

    # меню значка в лотке (StatusNotifierItem)
    "🔊  Показать окно": "🔊  Show window",
    "📂  Открыть файл…": "📂  Open file…",
    "📋  Озвучить буфер обмена": "📋  Speak the clipboard",
    "⚙️  Настройки": "⚙️  Settings",
    "ℹ️  О программе": "ℹ️  About",
    "✖  Выход": "✖  Quit",

    # открытие файла
    "Открыть файл…": "Open file…",
    "Открыть файл для озвучки": "Open a file to read aloud",
    "Озвучить": "Read aloud",
    "Книги FB2": "FB2 books",
    "Текстовые файлы": "Text files",
    "Все файлы": "All files",

    # поле ввода комбинации
    "Кликните для выбора…": "Click to choose…",

    # меню виджета
    "Озвучить буфер обмена": "Speak the clipboard",
    "Озвучить выделенное": "Speak the selection",
    # «Остановить» уже переведён выше, у кнопок плеера: второй такой же ключ
    # молча затирал первый, и правка в одном месте терялась.
    "Настройки": "Settings",
    "О программе": "About",
    "Выход": "Quit",
    "Меню": "Menu",

    # открытие файлов
    "Не удалось открыть файл": "Could not open the file",
    "Файл пустой": "The file is empty",
    "в архиве нет файла .fb2": "the archive has no .fb2 file",
    "в архиве tar нет файла .fb2": "the tar archive has no .fb2 file",
    "архив пустой": "the archive is empty",
    "это не FB2: нет корневого элемента FictionBook":
        "not an FB2 file: no FictionBook root element",
    "это не книга, а сохранённая веб-страница (HTML): "
    "у файла расширение .fb2, но внутри сайт":
        "this is not a book but a saved web page (HTML): "
        "the file is named .fb2, but it contains a site",
    "не удалось распаковать zstd: {error}": "could not decompress zstd: {error}",
    "это не FB2 или файл повреждён: {error}":
        "not an FB2 file or the file is damaged: {error}",
    "файл не найден: {path}": "file not found: {path}",
    "файл пустой": "the file is empty",
    "не текстовый файл: {name} (поддерживаются: {suffixes})":
        "not a text file: {name} (supported: {suffixes})",
    "неизвестный формат контейнера: {kind}": "unknown container format: {kind}",
    "нет поддержки zstd: установите zstd или python3-zstandard":
        "zstd is not supported: install zstd or python3-zstandard",
    "LZ4: неизвестный фрейм": "LZ4: unknown frame",
    "LZ4: пустой фрейм": "LZ4: empty frame",
    "LZ4: обрыв в длине литералов": "LZ4: truncated literal length",
    "LZ4: литералы за границей блока": "LZ4: literals beyond the block",
    "LZ4: обрыв в смещении совпадения": "LZ4: truncated match offset",
    "LZ4: некорректное смещение совпадения": "LZ4: invalid match offset",
    "LZ4: обрыв в длине совпадения": "LZ4: truncated match length",
    "LZ4: блок больше заявленного размера": "LZ4: block is larger than declared",
    "LZ4: обрыв в описании фрейма": "LZ4: truncated frame descriptor",
    "LZ4 legacy: ожидалась магия блока": "LZ4 legacy: block magic expected",
    "LZ4 legacy: обрыв в длине блока": "LZ4 legacy: truncated block length",
    "LZ4 legacy: пустой фрейм": "LZ4 legacy: empty frame",

    # сообщения движка
    "неизвестный язык модели: {lang} (есть: {known})":
        "unknown model language: {lang} (available: {known})",
    "неизвестный язык модели: {lang}": "unknown model language: {lang}",
    "Модель не найдена: {path}\n\nСкачайте её:\n  silero-tts-model download {lang}\n\n"
    "или вручную:\n  mkdir -p {parent}\n  wget -O {path} {url}":
        "Model not found: {path}\n\nDownload it:\n  silero-tts-model download {lang}\n\n"
        "or manually:\n  mkdir -p {parent}\n  wget -O {path} {url}",
    "Ошибка загрузки модели {file}: {error}": "Failed to load the model {file}: {error}",

    # CLI управления моделями
    "\r  {file}: {percent:3d}%  ({done} из {total} МБ)":
        "\r  {file}: {percent:3d}%  ({done} of {total} MB)",
    "каталог моделей: {directory}": "model directory: {directory}",
    "язык": "language",
    "файл": "file",
    "размер": "size",
    "состояние": "state",
    "установлена": "installed",
    "скачать: {command}": "download: {command}",
    "МБ": "MB",
    "неизвестный язык: {lang} (есть: {known})":
        "unknown language: {lang} (available: {known})",
    "неизвестный язык: {lang}": "unknown language: {lang}",
    "{lang}: уже установлена ({path})": "{lang}: already installed ({path})",
    "{lang}: качаю {url}": "{lang}: downloading {url}",
    "{lang}: не удалось скачать — {error}": "{lang}: download failed — {error}",
    "{lang}: готово ({path}, {size} МБ)": "{lang}: done ({path}, {size} MB)",
    "Модели Silero TTS: что установлено, что скачать.":
        "Silero TTS models: what is installed and what can be downloaded.",
    "список моделей и их состояние": "list models and their state",
    "скачать модели": "download models",
    "ЯЗЫК": "LANGUAGE",
    "коды языков: {codes}": "language codes: {codes}",
    "путь к файлу модели": "path to the model file",

    "Сброс настроек": "Reset settings",
    "Ошибка TTS": "TTS error",
    "Пауза": "Paused",
    "ПАУЗЫ": "PAUSES",
    "мс": "ms",
    "Перед репликой:": "Before a replica:",
    "Тишина перед прямой речью, начинающейся с тире":
        "Silence before direct speech that starts with a dash",
    "Между абзацами:": "Between paragraphs:",
    "Тишина на границе абзацев и перед заголовком главы":
        "Silence at a paragraph boundary and before a chapter heading",
    "Скорость недоступна: не найден rubberband":
        "Speed is unavailable: rubberband was not found",
    "{version} от {moment}{suffix}": "{version} of {moment}{suffix}",

    # консоль приложения и сообщения движка
    "Модель загружена. Голос: {voice}": "model loaded. Voice: {voice}",
    "Доступные голоса: {voices}": "available voices: {voices}",
    "Английская модель: голос {voice}, голосов {count}":
        "English model: voice {voice}, {count} voices",
    "Английской модели нет: латиница будет читаться по-русски.\n"
    "    Скачать: silero-tts-model download en":
        "no English model: Latin text will be read with Russian phonemes.\n"
        "    Download: silero-tts-model download en",
    "Ошибка: {error}": "error: {error}",
    "ошибка синтеза: {error}": "synthesis failed: {error}",
    "sounddevice не инициализируется: {error}": "sounddevice cannot start: {error}",
    "не удалось воспроизвести: {error} (или используйте --wav-out)":
        "playback failed: {error} (or use --wav-out)",
    "неожиданная разрядность вывода rubberband: {bits} бит":
        "unexpected rubberband output width: {bits} bit",
    "rubberband завершился с кодом {code}: {output}":
        "rubberband exited with code {code}: {output}",
    "Соединение успешно! Ответ ИИ: '{answer}'": "connection works! AI answer: '{answer}'",
    "Ошибка подключения: {error}": "connection failed: {error}",
    "старше": "older",

    # командная строка silero-tts-speak
    "язык: auto, {languages} (по умолчанию из конфига)":
        "language: auto, {languages} (default from the config)",
    "скорость в шкале speech-dispatcher: -100…+100 (приходит как 0.00, поэтому принимаем float)":
        "speed on the speech-dispatcher scale: -100…+100 "
        "(it arrives as 0.00, so a float is accepted)",
    "переключать язык по письменности: латиница — английской моделью, кириллица — русской":
        "switch the language by script: Latin with the English model, "
        "Cyrillic with the Russian one",
    "не хватает Python-модуля: {module}. Установите: sudo apt install python3-torch python3-numpy":
        "a Python module is missing: {module}. Install it: "
        "sudo apt install python3-torch python3-numpy",
    "слишком длинный текст: {count} символов (лимит {limit})":
        "the text is too long: {count} characters (limit {limit})",
    "silero-tts-speak: смешанный текст, голос {voice} → {plan}":
        "silero-tts-speak: mixed text, voice {voice} → {plan}",
    "silero-tts-speak: язык {language}, голос {voice}":
        "silero-tts-speak: language {language}, voice {voice}",
    "silero-tts-speak: скорость {speed:.2f}×":
        "silero-tts-speak: speed {speed:.2f}×",
    "silero-tts-speak: скорость недоступна — {error}":
        "silero-tts-speak: speed control is unavailable — {error}",
    "{lang}: модель не установлена (silero-tts-model download {lang})":
        "{lang}: the model is not installed (silero-tts-model download {lang})",

    # окно настроек: язык и поиск голоса
    "Язык:": "Language:",
    "Поиск голоса…": "Search for a voice…",
    "Голос": "Voice",
    "показано {shown} из {total}": "showing {shown} of {total}",
    "💡 Кликните по полю и нажмите желаемую комбинацию клавиш.\n"
    "Работает только на X11 / XWayland.":
        "💡 Click the field and press the combination you want.\n"
        "Works on X11 / XWayland only.",
    "💡 При недоступности ИИ или отключении опции применяется быстрый "
    "встроенный офлайн-фонетизатор.":
        "💡 When the AI is unreachable or the option is off, a fast built-in "
        "offline phonetiser is used.",
    "Нажмите новую клавишу…": "Press the new key…",
}


def pot_msgids() -> list[str]:
    """Все msgid из .pot, с раскрытыми переносами строк C-стиля."""
    raw = POT.read_text(encoding="utf-8")
    result: list[str] = []
    for block in re.findall(r'(?m)^msgid ((?:"(?:[^"\\]|\\.)*"\n?)+)', raw):
        text = "".join(re.findall(r'"((?:[^"\\]|\\.)*)"', block))
        if not text:
            continue
        result.append(
            text.replace("\\n", "\n").replace('\\"', '"').replace("\\\\", "\\")
        )
    return result


def po_quote(text: str) -> str:
    escaped = text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return f'"{escaped}"'


def _stale_sources() -> list[str]:
    """Файлы новее .pot: значит, каталог собран из устаревшего извлечения.

    Молча перевод пропадает только в интерфейсе, и заметить это можно случайно;
    поэтому расхождение — ошибка, а не предупреждение.
    """
    if not POT.exists():
        return ["(нет .pot)"]
    pot_time = POT.stat().st_mtime
    root = pathlib.Path(__file__).resolve().parent.parent / "silero_tts_reader"
    stale = [
        str(path.relative_to(root.parent))
        for path in root.rglob("*.py")
        if path.stat().st_mtime > pot_time
    ]
    return sorted(stale)


def main() -> int:
    stale = _stale_sources()
    if stale:
        print("ПОТ УСТАРЕЛ (сначала запустите packaging/i18n-wrap.py):", file=sys.stderr)
        for name in stale:
            print(f"  {name}", file=sys.stderr)
        return 1

    ids = pot_msgids()
    # Переводы с экранированными переводами строк сверяем в раскрытом виде
    normalized = {}
    for key, value in TRANSLATIONS.items():
        normalized[key] = value
        normalized[key.replace("\r", "\\r")] = value
        normalized[key.replace("\\r", "\r")] = value
    TRANSLATIONS.update(normalized)
    missing = [msg for msg in ids if msg not in TRANSLATIONS]
    if missing:
        print("НЕТ ПЕРЕВОДА:", file=sys.stderr)
        for msg in missing:
            print(f"  {msg!r}", file=sys.stderr)
        return 1

    PO_DIR.mkdir(parents=True, exist_ok=True)
    lines = [
        'msgid ""',
        'msgstr ""',
        '"Project-Id-Version: silero-tts-reader\\n"',
        '"Language: en_US\\n"',
        '"MIME-Version: 1.0\\n"',
        '"Content-Type: text/plain; charset=UTF-8\\n"',
        '"Content-Transfer-Encoding: 8bit\\n"',
        '"Plural-Forms: nplurals=2; plural=(n != 1);\\n"',
        "",
    ]
    for msg in ids:
        lines += [f"msgid {po_quote(msg)}", f"msgstr {po_quote(TRANSLATIONS[msg])}", ""]
    PO.write_text("\n".join(lines), encoding="utf-8")
    print(f"  записан {PO} ({len(ids)} строк)")

    subprocess.run(
        ["msgfmt", "--check", "--statistics", "-o", str(MO), str(PO)], check=True
    )
    print(f"  собран {MO}")

    import gettext

    with MO.open("rb") as handle:
        catalog = gettext.GNUTranslations(handle)
    untranslated = [msg for msg in ids if catalog.gettext(msg) == msg]
    if untranslated:
        print(f"не применилось переводов: {len(untranslated)}", file=sys.stderr)
        for msg in untranslated[:5]:
            print(f"  {msg!r}", file=sys.stderr)
        return 1
    print(f"  проверено: переведены все {len(ids)} строк")
    return 0


if __name__ == "__main__":
    sys.exit(main())
