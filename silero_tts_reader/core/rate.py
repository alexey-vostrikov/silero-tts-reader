"""Изменение скорости речи без изменения тона — через rubberband-cli.

Пакет зависит от ``rubberband-cli`` (он есть в архиве Ubuntu), а не от
Python-обёртки ``pyrubberband``: её в архиве нет, и раньше из-за этого
регулировка скорости молча не работала в установленной системе.

Модуль работает с float32-массивами и WAV в памяти: ничего не пишет на диск,
кроме временных файлов, которые сразу удаляются.
"""
from __future__ import annotations

import shutil
import struct
import subprocess
import tempfile
from pathlib import Path

import numpy as np

# Минимум и максимум, которые имеет смысл предлагать пользователю.
MIN_SPEED = 0.5
MAX_SPEED = 3.0

_RUBBERBAND = shutil.which("rubberband")


def available() -> bool:
    """Есть ли rubberband-cli в системе."""
    return _RUBBERBAND is not None


def _write_float_wav(path: Path, samples: np.ndarray, sample_rate: int) -> None:
    """WAV с 32-битным float (формат 3) — без потери точности на входе.

    Штатный ``wave`` пишет только целые сэмплы, а переводить float → int16
    перед растяжением значит терять разрядность дважды: на входе и на выходе.
    libsndfile внутри rubberband читает float-WAV без проблем.
    """
    data = np.asarray(samples, dtype="<f4").tobytes()
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF", 36 + len(data), b"WAVE",
        b"fmt ", 16, 3, 1, sample_rate, sample_rate * 4, 4, 32,
        b"data", len(data),
    )
    path.write_bytes(header + data)


def _read_float_wav(path: Path) -> tuple[np.ndarray, int]:
    """Прочитать WAV, который вернул rubberband (float32 или int16).

    Свой разбор RIFF вместо ``wave``: штатный модуль отказывается читать
    формат 3 (IEEE float), а rubberband на float-входе отдаёт именно его.
    """
    raw = path.read_bytes()
    if raw[:4] != b"RIFF" or raw[8:12] != b"WAVE":
        raise ValueError("rubberband вернул не WAV")

    rate = 0
    channels = 1
    width = 4
    data = b""
    pos = 12
    while pos + 8 <= len(raw):
        chunk_id = raw[pos:pos + 4]
        (size,) = struct.unpack("<I", raw[pos + 4:pos + 8])
        body = raw[pos + 8:pos + 8 + size]
        if chunk_id == b"fmt ":
            _tag, channels, rate, _byte_rate, _align, bits = struct.unpack("<HHIIHH", body[:16])
            width = bits // 8
        elif chunk_id == b"data":
            data = body
        pos += 8 + size + (size & 1)   # чанки выравниваются по чётному байту

    if not data:
        raise ValueError("в WAV нет данных")

    if width == 4:
        samples = np.frombuffer(data, dtype="<f4")
    elif width == 2:
        samples = np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768.0
    else:
        raise ValueError(f"неожиданная разрядность вывода rubberband: {width * 8} бит")

    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1)
    return samples.astype(np.float32), rate


def time_stretch(samples: np.ndarray, sample_rate: int, speed: float) -> np.ndarray:
    """Изменить скорость в ``speed`` раз, сохранив тон.

    ``speed`` > 1 — быстрее, < 1 — медленнее. Значения вне 0.5–3.0 обрезаются:
    за этими пределами речь превращается в неразборчивый шум.
    """
    if not available():
        raise RuntimeError(
            "rubberband не найден: sudo apt install rubberband-cli"
        )

    speed = float(max(MIN_SPEED, min(MAX_SPEED, speed)))
    audio = np.asarray(samples, dtype=np.float32).reshape(-1)
    if audio.size == 0 or abs(speed - 1.0) < 1e-3:
        return audio

    with tempfile.TemporaryDirectory(prefix="silero-rate-") as tmp:
        source = Path(tmp) / "in.wav"
        target = Path(tmp) / "out.wav"
        _write_float_wav(source, audio, sample_rate)
        result = subprocess.run(
            # -T (tempo) меняет скорость, не трогая высоту тона
            [_RUBBERBAND, "-q", "-T", f"{speed:.4f}", str(source), str(target)],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0 or not target.is_file():
            raise RuntimeError(
                f"rubberband завершился с кодом {result.returncode}: "
                f"{(result.stderr or result.stdout).strip()[:200]}"
            )
        stretched, _ = _read_float_wav(target)
    return stretched
