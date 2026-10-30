"""Configuration manager — loads and saves settings to ~/.config/silero-tts-reader/config.json."""
import json
import os
from pathlib import Path
from typing import Any

from ..models import DEFAULT_LANG, MODELS, model_path as default_model_path


CONFIG_DIR = Path.home() / ".config" / "silero-tts-reader"
CONFIG_FILE = CONFIG_DIR / "config.json"

DEFAULT_MODEL_PATH = str(default_model_path(DEFAULT_LANG))

# Языки, для которых конфиг хранит путь к модели и выбранный голос.
DEFAULT_MODELS = {lang: str(default_model_path(lang)) for lang in MODELS}
DEFAULT_SPEAKERS = {lang: MODELS[lang].default_speaker for lang in MODELS}

DEFAULT_SYSTEM_PROMPT = (
    "Ты — генератор текста для синтеза речи (Silero TTS).\n"
    "Твоя СТРОГАЯ задача: заменять ВСЕ иностранные, латинские слова, термины и бренды "
    "на их точную русскую фонетическую транскрипцию кириллицей и ОБЯЗАТЕЛЬНО ставить знак плюс '+' "
    "ПОСЛЕ ударной гласной буквы в транслитерированных словах (например: 'Python' -> 'Па+йтон', 'Google' -> 'Гу+гл', 'JavaScript' -> 'Джаваскри+пт', 'Windows' -> 'Ви+ндовс', 'GitHub' -> 'Гитха+б').\n"
    "СТРОГИЕ ПРАВИЛА:\n"
    "1. НЕ пиши никаких вступлений, пояснений или разметки (ЗАПРЕЩЕНО писать 'Вот текст:', 'Результат:').\n"
    "2. НЕ оставляй оригинальные латинские слова в скобках.\n"
    "3. В итоговом ответе НЕ ДОЛЖНО БЫТЬ ни одной латинской буквы (a-z, A-Z).\n"
    "4. Выдавай ТОЛЬКО чистый итоговый русский текст."
)

DEFAULTS: dict[str, Any] = {
    "hotkeys": {
        "speak_selection": "<ctrl>+<alt>+r",
        "speak_clipboard": "<ctrl>+<alt>+c",
        "stop": "<ctrl>+<alt>+s",
    },
    "tts": {
        # model_path/speaker оставлены ради старых конфигов: при чтении они
        # переезжают в models/speakers (см. _migrate).
        "model_path": DEFAULT_MODEL_PATH,
        "speaker": MODELS[DEFAULT_LANG].default_speaker,
        "models": dict(DEFAULT_MODELS),
        "speakers": dict(DEFAULT_SPEAKERS),
        "language": "auto",   # auto | ru | en | de | es | fr | ua
        # переключать язык внутри фразы по письменности (латиница — en)
        "mixed": False,
        "sample_rate": 48000,
    },
    "playback": {
        "speed": 1.0,
    },
    "widget": {
        "position_x": -1,   # -1 = auto (bottom-right)
        "position_y": -1,
    },
    "ui": {
        "theme": "system",  # "system", "dark", "light"
        "use_system_accent": True,
        # язык интерфейса: auto — из окружения (LANG/LC_ALL), иначе ru_RU/en_US
        "language": "auto",
    },
    "transliteration": {
        "enabled": True,
        "ignore_urls": True,
        "use_llm": False,
        "provider": "ollama",          # "ollama", "openai", "custom"
        "base_url": "http://localhost:11434/v1",
        "api_key": "ollama",
        "model": "qwen2.5",
        "timeout": 3.0,
        "system_prompt": DEFAULT_SYSTEM_PROMPT,
    },
}


class ConfigManager:
    """Manages persistent application configuration."""

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self._load()

    def _load(self) -> None:
        """Load config from disk, merging with defaults."""
        self._data = self._deep_merge(DEFAULTS, {})
        if CONFIG_FILE.exists():
            try:
                with CONFIG_FILE.open("r", encoding="utf-8") as f:
                    saved = json.load(f)
                self._data = self._deep_merge(DEFAULTS, saved)
                self._migrate(saved)
            except (json.JSONDecodeError, OSError):
                # Corrupted config — use defaults
                self._data = self._deep_merge(DEFAULTS, {})

    def _migrate(self, saved: dict[str, Any]) -> None:
        """Перенести одиночные model_path/speaker в многоязычные словари.

        Без этого старая настройка «модель» молча игнорировалась бы: после
        слияния с DEFAULTS в конфиге оказывались оба ключа, а код читает
        models["ru"], тогда как пользователь правил model_path.
        """
        tts = saved.get("tts")
        if not isinstance(tts, dict):
            return

        merged_tts = dict(self._data.get("tts") or {})
        raw_models, raw_speakers = tts.get("models"), tts.get("speakers")
        saved_models: dict[str, Any] = raw_models if isinstance(raw_models, dict) else {}
        saved_speakers: dict[str, Any] = raw_speakers if isinstance(raw_speakers, dict) else {}

        # models.ru из DEFAULTS уже заполнен, поэтому setdefault бесполезен:
        # старую настройку переносим принудительно, но только если новый ключ
        # в конфиге не задан.
        if tts.get("model_path") and DEFAULT_LANG not in saved_models:
            merged_tts["models"] = {**(merged_tts.get("models") or {}),
                                    DEFAULT_LANG: tts["model_path"]}
        if tts.get("speaker") and DEFAULT_LANG not in saved_speakers:
            merged_tts["speakers"] = {**(merged_tts.get("speakers") or {}),
                                      DEFAULT_LANG: tts["speaker"]}
        self._data["tts"] = merged_tts

    def save(self) -> None:
        """Persist current config to disk."""
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        with CONFIG_FILE.open("w", encoding="utf-8") as f:
            json.dump(self._data, f, ensure_ascii=False, indent=2)

    def reset_to_defaults(self) -> None:
        """Reset all settings to factory defaults and save."""
        self._data = self._deep_merge(DEFAULTS, {})
        self.save()

    # ── Accessors ────────────────────────────────────────────────────────────

    def get(self, *keys: str, default: Any = None) -> Any:
        """Get a nested value: get('tts', 'speaker')."""
        node = self._data
        for key in keys:
            if not isinstance(node, dict):
                return default
            node = node.get(key, default)
        return node

    def set(self, *keys_and_value) -> None:
        """Set a nested value: set('tts', 'speaker', 'xenia')."""
        *keys, value = keys_and_value
        node = self._data
        for key in keys[:-1]:
            node = node.setdefault(key, {})
        node[keys[-1]] = value

    # ── Convenience properties ────────────────────────────────────────────────

    @property
    def hotkey_speak_selection(self) -> str:
        return self.get("hotkeys", "speak_selection", default=DEFAULTS["hotkeys"]["speak_selection"])

    @hotkey_speak_selection.setter
    def hotkey_speak_selection(self, v: str) -> None:
        self.set("hotkeys", "speak_selection", v)

    @property
    def hotkey_speak_clipboard(self) -> str:
        return self.get("hotkeys", "speak_clipboard", default=DEFAULTS["hotkeys"]["speak_clipboard"])

    @hotkey_speak_clipboard.setter
    def hotkey_speak_clipboard(self, v: str) -> None:
        self.set("hotkeys", "speak_clipboard", v)

    @property
    def hotkey_stop(self) -> str:
        return self.get("hotkeys", "stop", default=DEFAULTS["hotkeys"]["stop"])

    @hotkey_stop.setter
    def hotkey_stop(self, v: str) -> None:
        self.set("hotkeys", "stop", v)

    @property
    def models(self) -> dict[str, str]:
        """Пути к моделям по языкам."""
        configured = self.get("tts", "models", default={})
        result = dict(DEFAULT_MODELS)
        if isinstance(configured, dict):
            result.update({str(k): str(v) for k, v in configured.items() if v})
        return result

    def model_for(self, language: str) -> str:
        return self.models.get(language, str(default_model_path(language))
                              if language in MODELS else DEFAULT_MODEL_PATH)

    @property
    def speakers(self) -> dict[str, str]:
        """Выбранный голос по языкам."""
        configured = self.get("tts", "speakers", default={})
        result = dict(DEFAULT_SPEAKERS)
        if isinstance(configured, dict):
            result.update({str(k): str(v) for k, v in configured.items() if v})
        return result

    def set_speaker(self, language: str, voice: str) -> None:
        """Запомнить голос для конкретного языка."""
        self.set("tts", "speakers", {**self.speakers, language: voice})
        if language == DEFAULT_LANG:
            # совместимость со старым одиночным ключом
            self.set("tts", "speaker", voice)

    def speaker_for(self, language: str) -> str:
        return self.speakers.get(
            language,
            MODELS[language].default_speaker if language in MODELS else MODELS[DEFAULT_LANG].default_speaker,
        )

    @property
    def language(self) -> str:
        """Язык синтеза: auto (по тексту) или конкретный код."""
        return str(self.get("tts", "language", default="auto"))

    @language.setter
    def language(self, v: str) -> None:
        self.set("tts", "language", str(v))

    @property
    def mixed(self) -> bool:
        """Переключать ли язык внутри одной фразы по письменности."""
        return bool(self.get("tts", "mixed", default=False))

    @mixed.setter
    def mixed(self, v: bool) -> None:
        self.set("tts", "mixed", bool(v))

    @property
    def model_path(self) -> str:
        """Путь к русской модели — совместимость со старыми конфигами."""
        return self.model_for(DEFAULT_LANG)

    @model_path.setter
    def model_path(self, v: str) -> None:
        self.set("tts", "models", {**self.models, DEFAULT_LANG: str(v)})
        self.set("tts", "model_path", str(v))

    @property
    def speaker(self) -> str:
        return self.speaker_for(DEFAULT_LANG)

    @speaker.setter
    def speaker(self, v: str) -> None:
        self.set("tts", "speakers", {**self.speakers, DEFAULT_LANG: str(v)})
        self.set("tts", "speaker", str(v))

    @property
    def sample_rate(self) -> int:
        return self.get("tts", "sample_rate", default=48000)

    @property
    def speed(self) -> float:
        return float(self.get("playback", "speed", default=1.0))

    @speed.setter
    def speed(self, v: float) -> None:
        self.set("playback", "speed", round(float(v), 2))

    @property
    def widget_position(self) -> tuple[int, int]:
        return (
            self.get("widget", "position_x", default=-1),
            self.get("widget", "position_y", default=-1),
        )

    @widget_position.setter
    def widget_position(self, pos: tuple[int, int]) -> None:
        self.set("widget", "position_x", pos[0])
        self.set("widget", "position_y", pos[1])

    @property
    def theme(self) -> str:
        return self.get("ui", "theme", default="system")

    @theme.setter
    def theme(self, v: str) -> None:
        if v in ("system", "dark", "light"):
            self.set("ui", "theme", v)

    @property
    def use_system_accent(self) -> bool:
        return bool(self.get("ui", "use_system_accent", default=True))

    @use_system_accent.setter
    def use_system_accent(self, v: bool) -> None:
        self.set("ui", "use_system_accent", bool(v))

    @property
    def ui_language(self) -> str:
        """Язык интерфейса: ``auto`` или код из i18n.AVAILABLE."""
        return str(self.get("ui", "language", default="auto"))

    @ui_language.setter
    def ui_language(self, v: str) -> None:
        self.set("ui", "language", str(v))

    @property
    def transliteration_enabled(self) -> bool:
        return bool(self.get("transliteration", "enabled", default=True))

    @transliteration_enabled.setter
    def transliteration_enabled(self, v: bool) -> None:
        self.set("transliteration", "enabled", bool(v))

    @property
    def ignore_urls(self) -> bool:
        return bool(self.get("transliteration", "ignore_urls", default=True))

    @ignore_urls.setter
    def ignore_urls(self, v: bool) -> None:
        self.set("transliteration", "ignore_urls", bool(v))

    @property
    def transliteration_use_llm(self) -> bool:
        return bool(self.get("transliteration", "use_llm", default=False))

    @transliteration_use_llm.setter
    def transliteration_use_llm(self, v: bool) -> None:
        self.set("transliteration", "use_llm", bool(v))

    @property
    def transliteration_provider(self) -> str:
        return str(self.get("transliteration", "provider", default="ollama"))

    @transliteration_provider.setter
    def transliteration_provider(self, v: str) -> None:
        self.set("transliteration", "provider", str(v))

    @property
    def transliteration_base_url(self) -> str:
        return str(self.get("transliteration", "base_url", default="http://localhost:11434/v1"))

    @transliteration_base_url.setter
    def transliteration_base_url(self, v: str) -> None:
        self.set("transliteration", "base_url", str(v))

    @property
    def transliteration_api_key(self) -> str:
        return str(self.get("transliteration", "api_key", default="ollama"))

    @transliteration_api_key.setter
    def transliteration_api_key(self, v: str) -> None:
        self.set("transliteration", "api_key", str(v))

    @property
    def transliteration_model(self) -> str:
        return str(self.get("transliteration", "model", default="qwen2.5"))

    @transliteration_model.setter
    def transliteration_model(self, v: str) -> None:
        self.set("transliteration", "model", str(v))

    @property
    def transliteration_timeout(self) -> float:
        return float(self.get("transliteration", "timeout", default=3.0))

    @transliteration_timeout.setter
    def transliteration_timeout(self, v: float) -> None:
        self.set("transliteration", "timeout", max(0.5, float(v)))

    @property
    def transliteration_system_prompt(self) -> str:
        return str(self.get("transliteration", "system_prompt", default=DEFAULT_SYSTEM_PROMPT))

    @transliteration_system_prompt.setter
    def transliteration_system_prompt(self, v: str) -> None:
        self.set("transliteration", "system_prompt", str(v))

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _deep_merge(base: dict, override: dict) -> dict:
        """Recursively merge override into base, returning a new dict."""
        result = dict(base)
        for key, value in override.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = ConfigManager._deep_merge(result[key], value)
            else:
                result[key] = value
        return result
