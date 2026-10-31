"""Автоматическая сверка corrections.json по трём источникам.

Запуск: python3 tools/verify-corrections.py

Словарь russian_g2p (данные), ruaccent (контекстная модель) и акцентор Silero
(по звуку). Где все три согласны с правкой — она почти наверняка верна; где
расходятся — нужен глаз человека.
"""
import json
import os
import sys
from pathlib import Path

import numpy as np
import torch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from silero_tts_reader.core.accent import Accentor  # noqa: E402

MODEL = os.path.expanduser("~/.local/share/silero-tts-reader/v5_ru.pt")
model = torch.package.PackageImporter(MODEL).load_pickle("tts_models", "model")
RATE = 48000

corrections = json.loads(
    (ROOT / "silero_tts_reader/assets/stress/corrections.json").read_text(
        encoding="utf-8"))
words = {k: v for k, v in corrections.items() if not k.startswith("_")}

dictionary = Accentor(mode="dict", directory=ROOT / "silero_tts_reader/assets/stress",
                      source="dictionary")
by_ruaccent = Accentor(mode="dict", directory=ROOT / "silero_tts_reader/assets/stress",
                       source="ruaccent")


def synth(text):
    with torch.no_grad():
        audio = model.apply_tts(
            text=text, speaker="xenia", sample_rate=RATE,
            put_accent=True, put_yo=True, put_stress_homo=True,
        )
    return audio.numpy()


def contour(text):
    audio = synth(text).astype(np.float64)
    frame, hop = 2048, 480
    values = []
    for start in range(0, len(audio) - frame, hop):
        chunk = audio[start:start + frame]
        if np.sqrt(np.mean(chunk ** 2)) < 0.01:
            values.append(0.0)
            continue
        chunk = chunk - chunk.mean()
        corr = np.correlate(chunk, chunk, mode="full")[frame - 1:]
        low, high = int(RATE / 400), int(RATE / 70)
        peak = low + int(np.argmax(corr[low:high])) if high < len(corr) else 0
        values.append(RATE / peak if peak > 0 else 0.0)
    return np.array(values)


def diff(a, b):
    n = min(len(a), len(b))
    return float(np.abs(a[:n] - b[:n]).mean())


def mark_position(form):
    """Индекс ударной гласной по записи со знаком «+»."""
    plain = form.replace("+", "")
    return plain, form.index("+")


def model_reads_as(word, candidate):
    """Читает ли модель слово так же, как вариант candidate."""
    plain, _ = mark_position(candidate)
    bare = contour(plain)
    marked = contour(candidate)
    return diff(bare, marked) < 1e-6


print(f"  правок для сверки: {len(words)}\n")
print(f"  {'слово':16} {'наша правка':16} {'словарь':16} {'ruaccent':16} "
      f"{'модель'}")

agree_all = disagree = 0
need_eye = []
for word, form in words.items():
    from_dict = dictionary.accent_word(word)
    from_ra = by_ruaccent.accent_text(word).strip()
    # Модель: совпадает ли её самостоятельное чтение с нашей правкой.
    model_same = model_reads_as(word, form)

    marks = []
    marks.append("=" if from_dict == form else f"≠{from_dict or '—'}")
    marks.append("=" if from_ra == form else f"≠{from_ra}")
    marks.append("=" if model_same else "≠")

    ok = from_dict == form and from_ra == form and model_same
    if ok:
        agree_all += 1
    else:
        disagree += 1
        need_eye.append(word)

    print(f"  {word:16} {form:16} {marks[0]:16} {marks[1]:16} {marks[2]}")

print(f"\n  все три источника согласны с правкой: {agree_all}")
print(f"  есть расхождения (нужен глаз): {disagree} — {need_eye}")
