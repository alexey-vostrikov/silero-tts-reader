"""CLI для speech-dispatcher: синтез текста в WAV и воспроизведение.

Вызывается generic-модулем speech-dispatcher (см. ``packaging/speechd/``),
но годится и вручную:

    silero-tts-speak "привет, мир"                 # синтез + воспроизведение
    silero-tts-speak --voice aidar --rate 1.25 ... # другой голос и темп
    silero-tts-speak --wav-out /tmp/out.wav ...    # только файл, без звука
    echo "текст" | silero-tts-speak -              # текст со stdin

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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="silero-tts-speak",
        description="Синтез речи Silero TTS из командной строки (для speech-dispatcher).",
    )
    parser.add_argument("text", nargs="*", help="текст; «-» или пусто — читать stdin")
    parser.add_argument("--voice", "-v", default=None, help="голос (xenia, aidar, eugene...)")
    parser.add_argument("--rate", "-r", type=float, default=1.0, help="темп 0.5–3.0")
    parser.add_argument("--wav-out", "-o", default=None, help="записать WAV, не воспроизводить")
    parser.add_argument("--model", default=None, help="путь к модели (по умолчанию из конфига)")
    parser.add_argument("--list-voices", action="store_true", help="показать голоса и выйти")
    args = parser.parse_args(argv)

    config = ConfigManager()
    model_path = args.model or config.model_path
    voice = args.voice or config.speaker

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

    engine = TTSEngine(model_path=model_path, sample_rate=config.sample_rate)
    try:
        engine.load()
    except TTSLoadError as exc:
        return _fail(str(exc))

    if args.list_voices:
        print("\n".join(engine.get_available_speakers()))
        return 0

    text = _read_text(args).strip()
    if not text:
        return _fail("пустой текст")
    if len(text) > MAX_CHARS:
        return _fail(f"слишком длинный текст: {len(text)} символов (лимит {MAX_CHARS})")

    speakers = engine.get_available_speakers()
    if voice not in speakers and speakers:
        voice = speakers[0]

    segments = engine.split_into_segments(text)
    rate = max(0.5, min(3.0, args.rate))

    # Движок печатает отладочные строки в stdout — уводим их в stderr,
    # чтобы не мешать тому, кто читает наш вывод.
    loud = io.StringIO()
    try:
        with contextlib.redirect_stdout(loud):
            chunks = [engine.synthesize(segment, voice) for segment in segments]
    except Exception as exc:  # noqa: BLE001 — любое падение синтеза объясняем словами
        print(loud.getvalue(), file=sys.stderr)
        return _fail(f"ошибка синтеза: {exc}")

    import numpy as np

    audio = np.concatenate(chunks) if len(chunks) > 1 else chunks[0]

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

    if abs(rate - 1.0) > 1e-3:
        try:
            audio = _apply_rate(audio, engine.sample_rate, rate)
        except Exception:  # noqa: BLE001 — темп не критичен, говорим как есть
            pass

    try:
        _play(audio, engine.sample_rate)
    except Exception as exc:  # noqa: BLE001 — нет звукового сервера или устройства
        return _fail(f"не удалось воспроизвести: {exc} (или используйте --wav-out)")
    return 0


def _apply_rate(samples, sample_rate: int, rate: float):
    """Изменить темп через rubberband (зависимость пакета), иначе без изменений."""
    import numpy as np
    import pyrubberband

    return pyrubberband.time_stretch(samples, sample_rate, rate) if np.size(samples) else samples

if __name__ == "__main__":
    sys.exit(main())
