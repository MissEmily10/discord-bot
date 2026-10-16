"""
Звуки роликов — синтезированные (без чужих файлов), мягкие, в духе Discord:
клик, печать, «поп» сообщения, отправка, успех, ошибка, уведомление, щелчок шага.

Свои файлы: положи WAV (моно или стерео, 44.1 кГц) в scripts/promo/sfx/<имя>.wav —
он заменит синтезированный звук с тем же именем.
"""

import array
import math
import pathlib
import random
import wave

RATE = 44100
SFX_DIR = pathlib.Path(__file__).resolve().parent / "sfx"


def _env(i, n, attack=0.004, release=None):
    t = i / RATE
    a = min(1.0, t / attack) if attack else 1.0
    if release:
        a *= math.exp(-t / release)
    return a


def _tone(freqs, dur, release, vol=0.5, attack=0.004, sweep=None):
    n = int(dur * RATE)
    out = []
    for i in range(n):
        t = i / RATE
        v = 0.0
        for k, f in enumerate(freqs):
            if sweep:
                f = f * (1 + sweep * t / dur)
            v += math.sin(2 * math.pi * f * t) / (k + 1)
        out.append(v * _env(i, n, attack, release) * vol)
    return out


def _noise(dur, release, vol=0.3, lowpass=0.2, seed=1):
    rng = random.Random(seed)
    n = int(dur * RATE)
    out, prev = [], 0.0
    for i in range(n):
        prev += (rng.uniform(-1, 1) - prev) * lowpass
        out.append(prev * _env(i, n, 0.002, release) * vol)
    return out


def _mixin(a, b, offset=0):
    for i, v in enumerate(b):
        j = i + offset
        if j >= len(a):
            a.extend([0.0] * (j - len(a) + 1))
        a[j] += v
    return a


def synth(name):
    if name == "click":
        s = _tone([1900], 0.045, 0.012, 0.35)
        return _mixin(s, _noise(0.03, 0.006, 0.35, 0.6, seed=3))
    if name == "key":
        return _mixin(_noise(0.025, 0.005, 0.18, 0.5, seed=random.randint(1, 9999)),
                      _tone([random.choice((2600, 2900, 3200))], 0.02, 0.004, 0.05))
    if name == "pop":       # новое сообщение
        return _tone([620, 1240], 0.12, 0.05, 0.38, sweep=0.55)
    if name == "send":
        return _mixin(_noise(0.18, 0.06, 0.22, 0.08, seed=5), _tone([740], 0.09, 0.03, 0.18, sweep=0.6))
    if name == "success":
        a = _tone([1046.5, 2093], 0.35, 0.12, 0.3)
        return _mixin(a, _tone([1318.5, 2637], 0.4, 0.14, 0.3), int(0.09 * RATE))
    if name == "error":
        a = _tone([196, 392, 588], 0.16, 0.06, 0.35)
        return _mixin(a, _tone([174.6, 349, 524], 0.2, 0.07, 0.35), int(0.13 * RATE))
    if name == "notify":
        a = _tone([880, 1760], 0.5, 0.16, 0.25)
        return _mixin(a, _tone([1318.5, 2637], 0.55, 0.18, 0.22), int(0.12 * RATE))
    if name == "tick":      # смена шага в колонке
        return _tone([1500, 3000], 0.06, 0.02, 0.12)
    if name == "whoosh":    # открылось окно / карточка
        return _noise(0.28, 0.09, 0.2, 0.05, seed=7)
    if name == "glitch":
        rng = random.Random(11)
        out = []
        for k in range(6):
            out = _mixin(out, _tone([rng.uniform(200, 1800)], 0.04, 0.02, 0.18), int(k * 0.035 * RATE))
        return _mixin(out, _noise(0.22, 0.08, 0.25, 0.9, seed=13))
    raise KeyError(name)


def _load(name):
    path = SFX_DIR / f"{name}.wav"
    if not path.exists():
        return None
    with wave.open(str(path)) as w:
        ch, width, rate = w.getnchannels(), w.getsampwidth(), w.getframerate()
        data = w.readframes(w.getnframes())
    if width != 2:
        return None
    a = array.array("h", data)
    vals = [a[i] / 32768 for i in range(0, len(a), ch)]
    if rate != RATE:
        step = rate / RATE
        vals = [vals[int(i * step)] for i in range(int(len(vals) / step))]
    return vals


_cache = {}


def get(name):
    if name == "key":           # каждая клавиша чуть другая
        return _load("key") or synth("key")
    if name not in _cache:
        _cache[name] = _load(name) or synth(name)
    return _cache[name]


def mix(events, total, path, volume=0.9):
    n = int((total + 1) * RATE)
    track = array.array("f", bytes(4 * n))
    last_key = -1.0
    for t, name in sorted(events):
        if name == "key":
            if t - last_key < 0.05:
                continue
            last_key = t
        snd = get(name)
        start = int(t * RATE)
        for i, v in enumerate(snd):
            j = start + i
            if j < n:
                track[j] += v
    out = array.array("h", (max(-32767, min(32767, int(math.tanh(v * volume) * 32767))) for v in track))
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(out.tobytes())
