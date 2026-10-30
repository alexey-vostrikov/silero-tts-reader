"""CLI для speech-dispatcher: синтез текста в WAV и воспроизведение.

Вызывается generic-модулем speech-dispatcher (см. ``packaging/speechd/``),
но годится и вручную:

    silero-tts-speak "привет, мир"                 # синтез + воспроизведение
    silero-tts-speak --voice aidar --rate 1.25 ... # другой голос и темп
    silero-tts-speak --lang en "hello there"       # английская модель
    silero-tts-speak --mixed "synthesize и русский"  # язык по письменности
    silero-tts-speak --rate 1.5 "быстрее в полтора раза"
    silero-tts-speak --wav-out /tmp/out.wav ...    # только файл, без звука
    echo "текст" | silero-tts-speak -              # текст со stdin

Язык берётся из ``--lang``, затем из конфига (``tts.language``); если там
``auto`` — по письменности текста: кириллица → русская модель, латиница →
английская.

Код выхода 1 и короткое сообщение в stderr — если нет модели или зависимостей:
модуль speech-dispatcher печатает это в свой лог, и причина видна сразу.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import sys
import wave
from pathlib import Path

from .config.config_manager import ConfigManager
from .core import rate as rate_module
from .models import DEFAULT_LANG, MODELS, split_by_script

MAX_CHARS = 20_000


def _fail(message: str) -> int:
    print(f"silero-tts-speak: {message}", file=sys.stderr)
    return 1


def _read_text(args: argparse.Namespace) -> str:
    if args.text and args.text != ["-"]:
        return " ".join(args.text)
    return sys.stdin.read()


def _write_wav(path: Path, samples, sample_rate: int) -> None:
    """float32 [-1..1] → 16-битный PCM WAV (то, что понимает любой плеер)."""
    import numpy as np

    clipped = np.clip(samples, -1.0, 1.0)
    pcm = (clipped * 32767).astype("<i2")
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(pcm.tobytes())


def _play(samples, sample_rate: int) -> None:
    """Воспроизведение через sounddevice: блокирует поток до конца фразы.

    Именно блокировка нужна speech-dispatcher: модуль считает фразу озвученной,
    когда команда завершилась. При остановке speechd убивает процесс, и звук
    прекращается вместе с ним.
    """
    import sounddevice as sd

    sd.play(samples, sample_rate)
    sd.wait()


def _list_voices(engine, requested: str | None) -> int:
    """Показать голоса установленных моделей (или одной, если язык задан)."""
    languages = [requested] if requested in MODELS else list(MODELS)
    printed = False
    for lang in languages:
        speakers = engine.get_available_speakers(lang)
        if not speakers:
            print(f"{lang}: модель не установлена "
                  f"(silero-tts-model download {lang})", file=sys.stderr)
            continue
        print(f"[{lang}]")
        print("\n".join(speakers))
        printed = True
    return 0 if printed else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="silero-tts-speak",
        description="Синтез речи Silero TTS из командной строки (для speech-dispatcher).",
    )
    parser.add_argument("text", nargs="*", help="текст; «-» или пусто — читать stdin")
    parser.add_argument("--voice", "-v", default=None, help="голос (xenia, aidar, en_0...)")
    parser.add_argument("--lang", "-L", default=None,
                        help=f"язык: auto, {', '.join(MODELS)} (по умолчанию из конфига)")
    parser.add_argument("--rate", "-r", type=float, default=None,
                        help="скорость речи 0.5–3.0 (1.0 — как есть)")
    parser.add_argument("--spd-rate", type=float, default=None, metavar="N",
                        help="скорость в шкале speech-dispatcher: -100…+100 "
                             "(приходит как 0.00, поэтому принимаем float)")
    parser.add_argument("--wav-out", "-o", default=None, help="записать WAV, не воспроизводить")
    parser.add_argument("--model", default=None,
                        help="путь к русской модели (переопределяет конфиг)")
    parser.add_argument("--mixed", "-M", action="store_true",
                        help="переключать язык по письменности: латиница — английской моделью, "
                             "кириллица — русской")
    parser.add_argument("--list-voices", action="store_true", help="показать голоса и выйти")
    args = parser.parse_args(argv)

    config = ConfigManager()
    models = dict(config.models)
    if args.model:
        models[DEFAULT_LANG] = args.model

    # Импорт torch отложенный: без него CLI должен внятно сказать, чего не хватает.
    try:
        import numpy as np  # noqa: F401
        import torch  # noqa: F401
    except ImportError as exc:
        return _fail(
            f"не хватает Python-модуля: {exc.name}. Установите: "
            "sudo apt install python3-torch python3-numpy"
        )

    from .core.tts_engine import TTSEngine, TTSLoadError

    engine = TTSEngine(sample_rate=config.sample_rate, models=models)

    # --lang важнее конфига; auto означает «решить по тексту».
    requested = args.lang or (None if config.language == "auto" else config.language)
    if requested == "auto":
        requested = None

    if args.list_voices:
        return _list_voices(engine, requested)

    text = _read_text(args).strip()
    if not text:
        return _fail("пустой текст")
    if len(text) > MAX_CHARS:
        return _fail(f"слишком длинный текст: {len(text)} символов (лимит {MAX_CHARS})")

    mixed = args.mixed or bool(config.mixed)
    language = engine.resolve_language(text, requested)
    try:
        engine.load(language)
    except TTSLoadError as exc:
        return _fail(str(exc))

    voice = args.voice or config.speaker_for(language)
    if mixed and len(split_by_script(text)) > 1:
        plan = ", ".join(f"{lang or 'default'}: {chunk[:24]!r}"
                         for lang, chunk in split_by_script(text))
        print(f"silero-tts-speak: смешанный текст, голос {voice} → {plan}", file=sys.stderr)
    else:
        mixed = False
        print(f"silero-tts-speak: язык {language}, голос {voice}", file=sys.stderr)

    speed = _resolve_speed(args, config)

    # Движок печатает отладочные строки в stdout — уводим их в stderr,
    # чтобы не мешать тому, кто читает наш вывод.
    loud = io.StringIO()
    try:
        with contextlib.redirect_stdout(loud):
            if mixed:
                audio = engine.synthesize_mixed(text, voice, default_language=language)
            else:
                segments = engine.split_into_segments(text)
                chunks = [engine.synthesize(segment, voice, language) for segment in segments]
                audio = None
    except Exception as exc:  # noqa: BLE001 — любое падение синтеза объясняем словами
        print(loud.getvalue(), file=sys.stderr)
        return _fail(f"ошибка синтеза: {exc}")

    import numpy as np

    if audio is None:
        audio = np.concatenate(chunks) if len(chunks) > 1 else chunks[0]

    if abs(speed - 1.0) > 1e-3:
        try:
            audio = rate_module.time_stretch(audio, engine.sample_rate, speed)
            print(f"silero-tts-speak: скорость {speed:.2f}×", file=sys.stderr)
        except Exception as exc:  # noqa: BLE001 — без rubberband говорим как есть
            print(f"silero-tts-speak: скорость недоступна — {exc}", file=sys.stderr)

    if args.wav_out:
        _write_wav(Path(args.wav_out), audio, engine.sample_rate)
        return 0

    try:
        import sounddevice  # noqa: F401
    except ImportError:
        return _fail(
            "нет sounddevice — нечем воспроизводить (sudo apt install python3-sounddevice) "
            "или используйте --wav-out"
        )
    except Exception as exc:  # noqa: BLE001 — PortAudio без звукового сервера
        return _fail(f"sounddevice не инициализируется: {exc}")

    try:
        _play(audio, engine.sample_rate)
    except Exception as exc:  # noqa: BLE001 — нет звукового сервера или устройства
        return _fail(f"не удалось воспроизвести: {exc} (или используйте --wav-out)")
    return 0


def _resolve_speed(args: argparse.Namespace, config) -> float:
    """Скорость из --rate, --spd-rate или конфига.

    speech-dispatcher присылает темп в своей шкале: -100 (медленно) … 0 … +100
    (быстро), поэтому её переводим в множитель. Приоритет: --rate, затем
    --spd-rate, затем playback.speed из конфига.
    """
    if args.rate is not None:
        return max(rate_module.MIN_SPEED, min(rate_module.MAX_SPEED, float(args.rate)))
    if args.spd_rate is not None:
        return _spd_percent_to_speed(args.spd_rate)
    return max(rate_module.MIN_SPEED, min(rate_module.MAX_SPEED, float(config.speed)))


def _spd_percent_to_speed(percent: float) -> float:
    """Шкала speech-dispatcher (-100…+100) → множитель скорости.

    Отрицательные значения замедляют до 0.5, положительные ускоряют до 2.0:
    дальше речь либо неразборчива, либо обрывается на стыках сегментов.
    Значение приходит как float («0.00», «50.00»), поэтому округляем.
    """
    percent = max(-100, min(100, int(round(float(percent)))))
    if percent >= 0:
        return 1.0 + (percent / 100.0)      # 0 → 1.0, +100 → 2.0
    return 1.0 + (percent / 200.0)          # -100 → 0.5

if __name__ == "__main__":
    sys.exit(main())
