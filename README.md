# Silero TTS Reader 🎙️🔊

[![Python Version](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![GUI Framework](https://img.shields.io/badge/GUI-GTK3%20%2F%20PyGObject-green.svg)](https://pygobject.gnome.org/)
[![TTS Engine](https://img.shields.io/badge/TTS-Silero_v5-orange.svg)](https://silero.ai/)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-Linux_(X11%2FWayland)-lightgrey.svg)](https://www.kernel.org/)

**Silero TTS Reader** — это современное, легкое и бытрое Linux-приложение для мгновенного озвучивания любого выделенного текста или содержимого буфера обмена с использованием нейросетевых моделей **Silero TTS v5**.

Приложение работает в фоновом режиме через системный трей, управляется глобальными горячими клавишами и предоставляет плавный плавающий виджет управления воспроизведением с возможностью гибкой регулировки скорости, перемотки и умной транслитерации.

---

## 📸 Скриншоты

<div align="center">

### Плавающий виджет воспроизведения
![Плавающий виджет](assets/screenshots/01_floating_widget.png)
*Плавающее оверлейное окно с регулятором скорости, интерактивной полосой прогресса, кнопками перемотки и быстрой паузы*

</div>

<br>

| ⌨️ Глобальные горячие клавиши | 🎭 Настройки голоса и аудио |
| :---: | :---: |
| ![Горячие клавиши](assets/screenshots/02_settings_hotkeys.png) | ![Голос и аудио](assets/screenshots/03_settings_voice.png) |
| *Настройка комбинаций под X11 и Wayland* | *Выбор диктора и живой предпросмотр речи* |

| 🎨 Внешний вид и темы | 🤖 ИИ & Транслитерация |
| :---: | :---: |
| ![Внешний вид](assets/screenshots/04_settings_appearance.png) | ![ИИ и транслитерация](assets/screenshots/05_settings_ai.png) |
| *Интеграция с системным акцентным цветом ОС* | *Настройка Ollama / OpenAI для фонетической записи* |

<div align="center">

### Иконка в системном трее
![Меню трея](assets/screenshots/06_tray_menu.png)
*Быстрое управление фоновым демоном и доступ к настройкам*

</div>

---

## ✨ Основные возможности

- ⚡ **Мгновенное озвучивание по хоткею**: Озвучивание выделенного в любой программе текста (`Ctrl+Alt+R`) или содержимого буфера обмена (`Ctrl+Alt+C`).
- ⏯ **Плавающий оверлейный виджет**:
  - Всегда поверх всех окон без захвата фокуса ввода.
  - Интерактивный таймлайн/прогресс-бар с возможностью перехода в любой момент аудио по клику мышью.
  - Кнопки быстрой перемотки назад и вперед (`◀◀` -5с / `▶▶` +5с).
  - Поддержка динамической паузы и возобновления.
- 🤖 **Интеллектуальная транслитерация (LLM + Offline)**:
  - **ИИ-транскрипция**: Интеграция с Ollama, OpenAI API или пользовательскими REST-эндпоинтами для адаптивного перевода иностранных брендов и терминов в кириллицу с точной простановкой ударений (например, `Python` → `Па+йтон`, `Google` → `Гу+гл`).
  - **Автономный фолбэк**: Встроенный правило-ориентированный словарь и фонетический транслитератор для 100% офлайн работы при отсутствии сети.
  - **100% Автономный конвертер чисел**: Локальное превращение любых арабских цифр, чисел, дат, процентов и дробей в числительные кириллицей (`2026` → `две тысячи двадцать шесть`, `100%` → `сто процентов`).
- 🎭 **Поддержка Silero TTS v5**:
  - Высококачественная генерация речи на русском языке (`xenia`, `kseniya`, `aidar`, `baya`, `eugene`).
  - Режим живого предпросмотра голоса прямо в настройках.
- 🎚 **Регулировка скорости (0.5x–3.0x)**: Изменение скорости речи в реальном времени с сохранением естественного тона голоса (требуется `rubberband`).
- 🎨 **Интеграция с оформлением системы**:
  - Автоматическое определение тёмной/светлой темы ОС.
  - Автоопределение и динамическое применение системного акцентного цвета (GNOME accent-color / портал Freedesktop, запасной путь — gsettings).
- 🐧 **Поддержка Wayland и X11**: Стабильная работа в Arch Linux, Ubuntu, Fedora, Debian и COSMIC Desktop.

---

## 🛠️ Системные требования

- **ОС**: Linux (Arch, Ubuntu/Debian, Fedora и др.)
- **Окружение**: X11 или Wayland (с включенным XWayland/evdev для глобальных хоткеев)
- **Python**: 3.10 или новее
- **Системные утилиты**:
  - `xdotool` и `xclip` — для эмуляции нажатий и считывания выделения
  - `rubberband-cli` — *(опционально)* для качественного изменения скорости речи

---

## 🚀 Быстрая установка

### Автоматическая установка (Рекомендуется)

Клонируйте репозиторий и запустите скрипт автоматической установки:

```bash
git clone https://github.com/Andrey4952/silero-tts-reader.git
cd silero-tts-reader
bash install.sh
```

Скрипт автоматически:
1. Определит ваш дистрибутив и установит системные зависимости (`xdotool`, `xclip`, `rubberband`).
2. Настроит права доступа udev для чтения клавиатурных событий глобальных хоткеев.
3. Скачает нейросетевую модель Silero TTS v5 (`v5_ru.pt`, ~145 МБ).
4. Создаст изолированное виртуальное окружение Python со всеми необходимыми пакетами.

### Ручная установка

#### 1. Установка системных зависимостей

* **Arch Linux / Manjaro**:
  ```bash
  sudo pacman -S xdotool xclip rubberband python-pyqt6 python-numpy python-pytorch-opt-cpu
  ```
* **Ubuntu / Debian**:
  ```bash
  sudo apt install xdotool xclip rubberband-cli python3-venv python3-pyqt6
  ```
* **Fedora**:
  ```bash
  sudo dnf install xdotool xclip rubberband python3-qt5
  ```

#### 2. Загрузка модели Silero TTS v5

```bash
mkdir -p ~/.local/share/silero-tts-reader
wget -O ~/.local/share/silero-tts-reader/v5_ru.pt \
  https://models.silero.ai/models/tts/ru/v5_ru.pt
```

#### 3. Настройка Python venv и зависимостей

```bash
python3 -m venv --system-site-packages .venv
source .venv/bin/activate
pip install -r requirements.txt pyrubberband
```

---

## 📦 Сборка и установка .deb

Пакет собирается без `debhelper` — достаточно `dpkg-deb` и `python3-pil` (для иконок):

```bash
./packaging/build-deb.sh          # результат: четыре .deb в dist/
VERSION=2.0.0 ./packaging/build-deb.sh   # версию можно задать явно
```

Пакетов четыре — раскладка по назначению:

| Пакет | Что внутри |
|---|---|
| `silero-tts-common` | настройки и конфиги: `config_manager`, движок синтеза, обработка текста (`/usr/lib/silero-tts/silero_tts_reader/{config,core}`) |
| `silero-tts-reader` | GTK-приложение: `/usr/bin/silero-tts-reader`, `.desktop`, иконки, user-unit |
| `silero-tts-speechd` | командная строка: `/usr/bin/silero-tts-speak` и `/usr/bin/silero-tts-speechd` |
| `speech-dispatcher-silero-tts` | конфигурация модуля `/etc/speech-dispatcher/modules/silero-tts.conf` |

`silero-tts-common` держит общий код, остальные три зависят от него точной версией
(`silero-tts-common (= <версия>)`), так что половинчатой установки не бывает.

Версия пакета детерминирована: тег `v2.1.0` на HEAD даёт `2.1.0`, при коммитах после тега —
`2.1.0+7.gabc1234`, без тегов вовсе — `<version из pyproject.toml>+gitabc1234`, а неубранные правки
добавляют `.dirty`. Установка и удаление:

```bash
sudo apt install ./dist/silero-tts-common_*.deb ./dist/silero-tts-reader_*.deb \
                 ./dist/silero-tts-speechd_*.deb ./dist/speech-dispatcher-silero-tts_*.deb
sudo apt remove silero-tts-reader silero-tts-speechd speech-dispatcher-silero-tts silero-tts-common
```

Что ставится: общий код в `/usr/lib/silero-tts`, запускаторы `/usr/bin/silero-tts-reader` и
`/usr/bin/silero-tts-speak`, `.desktop` в `/usr/share/applications`, иконки в теме `hicolor`,
user-unit `/usr/lib/systemd/user/silero-tts-reader.service` (не включается автоматически),
конфигурация модуля speech-dispatcher.

`Depends` пакета `silero-tts-reader` — только то, что реально есть в архиве Ubuntu: `python3-gi`,
`gir1.2-gtk-3.0`, `gir1.2-ayatanaappindicator3-0.1` (иконка в трее на Wayland) и `rubberband-cli`
(изменение скорости). Тяжёлые и необязательные для запуска `python3-torch` (движок синтеза, ~65 МБ в
пакете, ~117 МБ на диске) и `python3-sounddevice` (вывод звука), а также `python3-pynput` и
`python3-evdev` (глобальные хоткеи) — в `Recommends`: в Ubuntu 26.04 они есть в архиве, ставятся
одной командой, а запускатор проверяет их наличие и подсказывает её:

```bash
sudo apt install python3-torch python3-sounddevice python3-pynput python3-evdev
```

Сборка в CI — `.github/workflows/deb.yml`: обе джобы выполняются на `ubuntu-26.04`, а сборка идёт
внутри контейнера `ubuntu:26.04` (целевой дистрибутив). На каждый push/PR собираются оба пакета,
проверяется состав (`dpkg-deb --contents`), они ставятся на чистую систему (`dpkg -i` без
`--force-depends`), сверяется, что GTK инициализируется, запускатор даёт внятное сообщение об
отсутствующих pip-модулях, а `speech-dispatcher` загружает модуль `silero-tts`; на любой тег пакеты
прикрепляются к релизу. Локальная отладка workflow — `act`:

```bash
act -W .github/workflows/deb.yml -j deb     # конфигурация образов — в .actrc
```

## 🗣️ Интеграция со speech-dispatcher

Отдельный пакет `speech-dispatcher-silero-tts` делает голоса Silero доступными любому клиенту
speech-dispatcher: `spd-say`, Orca, режим чтения в Firefox, talkd-совместимые утилиты. Он собирается
тем же скриптом (`dist/speech-dispatcher-silero-tts_<версия>_all.deb`) и зависит от
`silero-tts-speechd` той же версии: конфигурация модуля и инструмент его включения разведены по
пакетам, чтобы обновлять их независимо.

Собственного C-модуля нет: используется штатный плагин `sd_generic`, а синтез и воспроизведение
выполняет `/usr/bin/silero-tts-speak`. Конфигурация модуля
`/etc/speech-dispatcher/modules/silero-tts.conf` лежит в `speech-dispatcher-silero-tts` (conffile),
команда управления `silero-tts-speechd` — в одноимённом пакете:

```bash
sudo apt install ./dist/silero-tts-common_*.deb ./dist/silero-tts-reader_*.deb \
                 ./dist/silero-tts-speechd_*.deb ./dist/speech-dispatcher-silero-tts_*.deb

sudo silero-tts-speechd enable     # добавить AddModule в speechd.conf (идемпотентно, с бэкапом)
silero-tts-speechd status          # подключён ли модуль
spd-say -O                         # в списке появится silero-tts
spd-say -m silero-tts -l ru "Проверка синтеза"
spd-say -m silero-tts -y FEMALE2 -l ru "Голос baya"
sudo silero-tts-speechd disable    # убрать ровно добавленный блок
```

Голоса: `FEMALE1` (xenia), `FEMALE2` (baya), `FEMALE3` (kseniya), `MALE1` (aidar), `MALE2` (eugene),
`MALE3` (random). Модулем по умолчанию модуль становится после `DefaultModule silero-tts` в
`speechd.conf`.

Темп и высота тона не передаются: модель Silero v5 их не поддерживает, а целочисленный `$RATE`
speech-dispatcher дал бы бессмысленную скорость. Ограничения прямые: без установленных `torch`,
`sounddevice` и модели (`~/.local/share/silero-tts-reader/v5_ru.pt`) `silero-tts-speak` завершится
кодом 1 с понятным сообщением, и speech-dispatcher напишет его в свой лог.

---

## 💻 Использование

### Запуск приложения

```bash
# Из папки репозитория
python run.py

# Или после установки скрипта в систему
silero-tts-reader
```

### Установка в систему и автозапуск

Для добавления приложения в меню приложений вашей ОС и автозапуска при входе в систему:

```bash
# Создание бинарного ярлыка в ~/.local/bin и .desktop файла
mkdir -p ~/.local/bin ~/.local/share/applications ~/.config/autostart

cat << 'EOF' > ~/.local/bin/silero-tts-reader
#!/usr/bin/env bash
exec /path/to/silero-tts-reader/.venv/bin/python /path/to/silero-tts-reader/run.py "$@"
EOF
chmod +x ~/.local/bin/silero-tts-reader

cp silero-tts-reader.desktop ~/.local/share/applications/
cp silero-tts-reader.desktop ~/.config/autostart/
```

Также доступен юнит **systemd** для фонового демона:
```bash
cp silero-tts-reader.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now silero-tts-reader.service
```

---

## ⌨️ Горячие клавиши по умолчанию

| Действие | Горячая клавиша | Описание |
| :--- | :---: | :--- |
| **Озвучить выделенный текст** | <kbd>Ctrl</kbd> + <kbd>Alt</kbd> + <kbd>R</kbd> | Захватывает выделенный в любом окне текст и начинает воспроизведение |
| **Озвучить буфер обмена** | <kbd>Ctrl</kbd> + <kbd>Alt</kbd> + <kbd>C</kbd> | Читает текст из буфера обмена |
| **Остановить воспроизведение** | <kbd>Ctrl</kbd> + <kbd>Alt</kbd> + <kbd>S</kbd> | Мгновенно останавливает речь и скрывает виджет |

*Все горячие клавиши можно легко переназначить в окне настроек приложения (иконка в трее → Настройки).*

---

## ⚙️ Конфигурация

Файл настроек автоматически сохраняется по пути `~/.config/silero-tts-reader/config.json`.

Пример конфигурации:
```json
{
  "hotkey_speak_selection": "<ctrl>+<alt>+r",
  "hotkey_speak_clipboard": "<ctrl>+<alt>+c",
  "hotkey_stop": "<ctrl>+<alt>+s",
  "tts": {
    "model_path": "/home/user/.local/share/silero-tts-reader/v5_ru.pt",
    "speaker": "xenia",
    "sample_rate": 48000,
    "speed": 1.0
  },
  "appearance": {
    "theme_mode": "system",
    "use_system_accent": true
  },
  "transliteration": {
    "enabled": true,
    "use_llm": true,
    "provider": "ollama",
    "base_url": "http://localhost:11434/v1",
    "model": "llama3"
  }
}
```

---

## 🏗️ Структура проекта

```text
silero-tts-reader/
├── assets/
│   └── screenshots/         # Скриншоты интерфейса
├── silero_tts_reader/
│   ├── config/              # Менеджер конфигурации JSON
│   ├── core/                # Ядро: TTS Engine, Audio Player, LLM & Number Transliteration
│   ├── ui/                  # GTK3 UI: трей, плавающий виджет, окно настроек
│   └── app.py               # Главный контроллер приложения
├── install.sh               # Скрипт автоустановки
├── run.py                   # Точка входа для запуска
├── pyproject.toml           # Метаданные пакета
├── requirements.txt         # Зависимости Python
└── silero-tts-reader.desktop# Ярлык приложения
```

---

## 📜 Лицензия

Проект распространяется под лицензией **MIT**. Подробности в файле [LICENSE](LICENSE).
