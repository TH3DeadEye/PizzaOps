"""Build the game's sound effects and music from scratch: pure Python, fixed seeds, byte-identical every time.

    uv run python tools/build_audio.py              # writes assets/game/audio/*.wav

No samples and no third-party audio: every sound is synthesised here from sine, triangle, square and noise
generators with simple envelopes and filters, so the audio is original and its source is this file.
"""

import array
import math
import random
import sys
import wave
from pathlib import Path

RATE = 22050
TAU = 2 * math.pi
OUT = Path(__file__).resolve().parents[1] / "assets" / "game" / "audio"


# ---------------------------------------------------------------------- building blocks

def midi(note):
    return 440.0 * 2 ** ((note - 69) / 12)


def blank(seconds):
    return [0.0] * round(seconds * RATE)


def add(buffer, sound, at=0.0, gain=1.0, wrap=False):
    """Mix ``sound`` into ``buffer`` at ``at`` seconds; with ``wrap`` a tail past the end continues at the start."""
    start, size = round(at * RATE), len(buffer)
    for index, value in enumerate(sound):
        position = start + index
        if position >= size:
            if not wrap:
                break
            position %= size
        buffer[position] += value * gain
    return buffer


def tone(freq, seconds, *, shape="sine", partials=((1.0, 1.0),), attack=0.004, decay=None, release=0.02, duty=0.5,
         sweep_to=None):
    """One note: summed partials of a waveform under an attack / exponential decay / release envelope.

    ``sweep_to`` glides the pitch exponentially to that frequency over the note (a kick drum's thump).
    """
    count = round(seconds * RATE)
    out = [0.0] * count
    phases = [0.0] * len(partials)
    for index in range(count):
        t = index / RATE
        current = freq if sweep_to is None else freq * (sweep_to / freq) ** (t / seconds)
        level = min(1.0, t / attack) if attack else 1.0
        if decay:
            level *= math.exp(-t / decay)
        if release and t > seconds - release:
            level *= max(0.0, (seconds - t) / release)
        value = 0.0
        for slot, (ratio, amplitude) in enumerate(partials):
            phase = phases[slot] = (phases[slot] + current * ratio / RATE) % 1.0
            if shape == "sine":
                value += amplitude * math.sin(TAU * phase)
            elif shape == "triangle":
                value += amplitude * (4 * abs(phase - 0.5) - 1)
            else:
                value += amplitude * (1.0 if phase < duty else -1.0)
        out[index] = value * level
    return out


def noise(seconds, rng):
    return [rng.uniform(-1.0, 1.0) for _ in range(round(seconds * RATE))]


def lowpass(samples, cutoff):
    alpha = 1 - math.exp(-TAU * cutoff / RATE)
    level, out = 0.0, []
    for value in samples:
        level += alpha * (value - level)
        out.append(level)
    return out


def highpass(samples, cutoff):
    return [value - low for value, low in zip(samples, lowpass(samples, cutoff))]


def envelope(samples, attack, decay):
    return [value * min(1.0, (index / RATE) / attack if attack else 1.0) * math.exp(-(index / RATE) / decay)
            for index, value in enumerate(samples)]


def seamless(samples, fade):
    """Fold the overhang after the loop length back onto the start, so the end flows into the beginning."""
    overlap = round(fade * RATE)
    body = samples[:len(samples) - overlap]
    for index in range(overlap):
        weight = index / overlap
        body[index] = samples[len(body) + index] * (1 - weight) + body[index] * weight
    return body


def bell(freq, seconds, decay):
    return tone(freq, seconds, partials=((1.0, 1.0), (2.76, 0.42), (5.40, 0.18), (8.93, 0.07)), attack=0.002, decay=decay)


# ---------------------------------------------------------------------- sound effects

def door_chime():
    out = blank(1.1)
    add(out, bell(midi(88), 0.9, 0.32), 0.0)        # E6
    add(out, bell(midi(84), 0.9, 0.38), 0.17, 0.9)  # C6
    return out


def register():
    rng = random.Random("register")
    out = blank(0.75)
    add(out, envelope(highpass(noise(0.03, rng), 1800), 0.001, 0.008), 0.0, 0.8)   # the drawer's clack
    add(out, envelope(highpass(noise(0.02, rng), 2500), 0.001, 0.006), 0.045, 0.6)
    add(out, bell(midi(96), 0.6, 0.16), 0.07, 0.7)                                 # ...ching
    add(out, bell(midi(103), 0.55, 0.12), 0.11, 0.45)
    return out


def ticket():
    rng = random.Random("ticket")
    return envelope(highpass(lowpass(noise(0.2, rng), 7000), 1500), 0.004, 0.05)


def oven_whoosh():
    rng = random.Random("whoosh")
    swell = [value * math.sin(math.pi * min(1.0, index / (0.85 * RATE))) for index, value in
             enumerate(lowpass(noise(0.85, rng), 700))]
    rumble = tone(70, 0.85, shape="triangle", attack=0.2, decay=0.5)
    return add(swell, rumble, 0.0, 0.35)


def order_bell():
    out = blank(1.4)
    add(out, bell(midi(100), 1.3, 0.42))  # a service-desk "ding"
    return out


def fail():
    out = blank(0.75)
    for at, note in ((0.0, 67), (0.24, 62)):  # a gentle descending "bwomp"
        add(out, lowpass(tone(midi(note) / 2, 0.36, shape="square", duty=0.4, attack=0.01, decay=0.25), 1200), at)
    return out


def restock():
    rng = random.Random("restock")
    out = tone(110, 0.38, sweep_to=48, attack=0.002, decay=0.11)                    # a crate set down
    return add(out, envelope(lowpass(noise(0.2, rng), 900), 0.001, 0.04), 0.0, 0.6)


def sting():
    out = blank(1.05)
    for at, note in ((0.0, 50), (0.32, 49)):  # dun... dun
        voice = tone(midi(note), 0.6, shape="square", duty=0.5, partials=((1.0, 1.0), (0.5, 0.6)), attack=0.008, decay=0.3)
        add(out, lowpass(voice, 900), at)
    return out


def click():
    return tone(1600, 0.06, attack=0.001, decay=0.012)


# ---------------------------------------------------------------------- loops

def oven_crackle():
    rng = random.Random("crackle")
    seconds = 4.0
    out = [value * 0.18 for value in lowpass(noise(seconds + 0.5, rng), 500)]          # the fire's breath
    t = 0.0
    while t < seconds + 0.4:
        t += rng.expovariate(14)
        pop = envelope(highpass(noise(rng.uniform(0.004, 0.02), rng), 1500), 0.0005, rng.uniform(0.002, 0.008))
        add(out, pop, t, rng.uniform(0.3, 1.0))
    return seamless(out, 0.5)


def room_tone():
    rng = random.Random("room")
    seconds = 8.0
    hum = lowpass(lowpass(noise(seconds + 1.0, rng), 160), 160)
    chatter = lowpass(highpass(noise(seconds + 1.0, rng), 300), 1200)
    out = []
    for index, (low, mid) in enumerate(zip(hum, chatter)):
        t = index / RATE
        murmur = 0.5 + 0.3 * math.sin(TAU * 0.31 * t) + 0.2 * math.sin(TAU * 0.83 * t + 1.3)
        out.append(low * 3.0 + mid * 0.25 * murmur)
    return seamless(out, 1.0)


CHORDS = {"Fmaj7": (53, 57, 60, 64), "Em7": (52, 55, 59, 62), "Dm7": (50, 53, 57, 60),
          "Cmaj7": (48, 52, 55, 59), "G7": (55, 59, 62, 65)}
FORM = ["Fmaj7", "Em7", "Dm7", "Cmaj7"] * 3 + ["Fmaj7", "Em7", "Dm7", "G7"]
# One-bar lead phrases as (swung eighth, note): E D C D E G over F and Dm; D B G A B over Em and C.
MOTIFS = ([(0, 76), (1, 74), (2, 72), (3, 74), (4, 76), (6, 79)], [(0, 74), (2, 71), (4, 67), (5, 69), (6, 71)])


def music():
    """A 16-bar lo-fi loop at 88 BPM: soft chords, a walking triangle bass, a plucked pulse arpeggio, brushed drums
    and a little vinyl crackle. Notes that ring past bar 16 wrap into bar 1, so the loop has no seam."""
    rng = random.Random("music")
    beat = 60 / 88
    bar = 4 * beat
    out = blank(len(FORM) * bar)
    cache = {}

    def voice(key, build):
        if key not in cache:
            cache[key] = build()
        return cache[key]

    def eighth(index):
        return (index // 2) * beat + (0.58 * beat if index % 2 else 0.0)  # swung eighths

    for number, name in enumerate(FORM):
        start = number * bar
        notes = CHORDS[name]
        for note in notes:
            pad = voice(("pad", note), lambda: lowpass(tone(midi(note), bar + 0.35, shape="triangle", attack=0.3, release=0.45), 1800))
            add(out, pad, start, 0.09, wrap=True)
        root = notes[0] - 12 if notes[0] < 54 else notes[0] - 24
        for at, length, step in ((0.0, 1.4 * beat, 0), (2 * beat, 1.2 * beat, 0), (3.5 * beat, 0.45 * beat, 7)):
            bass = voice(("bass", root + step, length), lambda: tone(midi(root + step), length, shape="triangle", attack=0.006, decay=0.5, release=0.05))
            add(out, bass, start + at, 0.42, wrap=True)
        if number >= 4:
            pattern = rng.sample(range(8), 5) if number < 8 or number >= 12 else []
            for index in sorted(pattern):
                note = notes[index % 4] + 12
                pluck = voice(("pluck", note), lambda: lowpass(tone(midi(note), 0.3, shape="square", duty=0.25, attack=0.004, decay=0.11), 2600))
                add(out, pluck, start + eighth(index), 0.11, wrap=True)
        if 8 <= number < 12:
            for index, note in MOTIFS[number % 2]:
                lead = voice(("lead", note), lambda: lowpass(tone(midi(note), 0.42, shape="square", duty=0.3, attack=0.01, decay=0.22), 2200))
                add(out, lead, start + eighth(index), 0.12, wrap=True)
        for hit in range(4):
            at = start + hit * beat
            if hit in (0, 2):
                add(out, voice("kick", lambda: tone(120, 0.26, sweep_to=42, attack=0.002, decay=0.09)), at, 0.55)
            else:
                snare = voice("snare", lambda: add(envelope(lowpass(noise(0.18, random.Random("snare")), 3500), 0.001, 0.05),
                                                   tone(185, 0.18, attack=0.001, decay=0.04), 0.0, 0.4))
                add(out, snare, at, 0.2)
        for index in range(8):
            hat = voice("hat", lambda: envelope(highpass(noise(0.05, random.Random("hat")), 6000), 0.001, 0.012))
            add(out, hat, start + eighth(index), 0.07 if index % 2 else 0.1)
    crackle = [value * 0.012 for value in lowpass(noise(len(out) / RATE + 0.5, rng), 3000)]
    t = 0.0
    while t < len(out) / RATE:
        t += rng.expovariate(3)
        add(crackle, envelope(highpass(noise(0.004, rng), 2000), 0.0003, 0.0015), t, rng.uniform(0.04, 0.1))
    add(out, seamless(crackle, 0.5))
    return lowpass(out, 6500)


SOUNDS = {  # name: (builder, peak level)
    "door_chime": (door_chime, 0.55), "register": (register, 0.5), "ticket": (ticket, 0.35), "oven_whoosh": (oven_whoosh, 0.45),
    "order_bell": (order_bell, 0.5), "fail": (fail, 0.4), "restock": (restock, 0.6), "sting": (sting, 0.5), "click": (click, 0.25),
    "oven_crackle": (oven_crackle, 0.4), "room_tone": (room_tone, 0.35), "music": (music, 0.6),
}


def render(name):
    """The 16-bit mono PCM bytes of one sound, peak-normalised."""
    builder, peak = SOUNDS[name]
    samples = builder()
    loudest = max(abs(value) for value in samples) or 1.0
    data = array.array("h", (round(max(-1.0, min(1.0, value * peak / loudest)) * 32767) for value in samples))
    if sys.byteorder == "big":
        data.byteswap()
    return data.tobytes()


def write(name, folder=OUT):
    folder.mkdir(parents=True, exist_ok=True)
    with wave.open(str(folder / f"{name}.wav"), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(RATE)
        out.writeframes(render(name))


def main(names=None):
    for name in names or SOUNDS:
        write(name)
        print(f"wrote {OUT / name}.wav")


if __name__ == "__main__":
    main(sys.argv[1:])
