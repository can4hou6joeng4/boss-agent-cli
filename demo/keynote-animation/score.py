"""Original 120 BPM instrumental. Six four-bar chapters match storyboard.json."""

import json
import subprocess
import wave
from pathlib import Path

import numpy as np

root = Path(__file__).resolve().parent
spec = json.loads((root / "storyboard.json").read_text())
rate = 48000
length = spec["duration"]
audio = np.zeros((rate * length, 2), dtype=np.float64)
rng = np.random.default_rng(20261008)
beat = 60 / spec["bpm"]


def hz(midi):
	return 440 * 2 ** ((midi - 69) / 12)


def add(at, signal, gain=1.0, pan=0.0):
	i = round(at * rate)
	n = min(len(signal), len(audio) - i)
	if n <= 0:
		return
	audio[i : i + n, 0] += signal[:n] * gain * np.sqrt((1 - pan) / 2)
	audio[i : i + n, 1] += signal[:n] * gain * np.sqrt((1 + pan) / 2)


def pluck(at, note, gain=0.07, pan=0.0, dur=1.5):
	t = np.arange(round(dur * rate)) / rate
	f = hz(note)
	env = (1 - np.exp(-t * 240)) * np.exp(-t * 5.2)
	w = np.sin(2 * np.pi * f * t) + 0.28 * np.sin(2 * np.pi * f * 2 * t) + 0.08 * np.sin(2 * np.pi * f * 3 * t)
	s = w * env
	add(at, s, gain, pan)
	add(at + beat * 0.75, s, gain * 0.2, -pan)
	add(at + beat * 1.5, s, gain * 0.07, pan)


def pad(at, chord, gain=0.023, dur=8):
	t = np.arange(round((dur + 1.5) * rate)) / rate
	env = np.minimum(1, t / 1.2) * np.clip((dur + 1.5 - t) / 2.0, 0, 1)
	for k, note in enumerate(chord):
		f = hz(note)
		s = (
			(np.sin(2 * np.pi * f * t) + 0.2 * np.sin(2 * np.pi * f * 2 * t))
			* env
			* (0.88 + 0.12 * np.sin(t * 1.7 + k))
		)
		add(at, s, gain, (-0.6 + k * 0.3))


def kick(at, gain):
	t = np.arange(round(rate * 0.48)) / rate
	phase = 2 * np.pi * (43 * t + (104 - 43) * (1 - np.exp(-t * 32)) / 32)
	s = np.sin(phase) * np.exp(-t * 12) * np.minimum(1, t * 300)
	add(at, s, gain)


def hat(at, gain=0.013, pan=0.25):
	t = np.arange(round(rate * 0.14)) / rate
	noise = rng.normal(0, 1, len(t))
	noise = np.r_[0, np.diff(noise)] * 0.5
	s = noise * np.exp(-t * 50) * np.minimum(1, t * 1500)
	add(at, s, gain, pan)


# Dmaj9 / Bm7 / Gmaj7 / Aadd9; three-note reveal motif.
chords = [
	[50, 57, 61, 66, 69],
	[47, 54, 57, 62, 66],
	[43, 50, 54, 59, 62],
	[45, 52, 57, 59, 64],
	[43, 50, 54, 59, 62],
	[50, 57, 61, 66, 69],
]
for chapter, chord in enumerate(chords):
	start = chapter * 8
	pad(start, chord)
	for b in range(16):
		at = start + b * beat
		energy = [0.03, 0.075, 0.085, 0.09, 0.05, 0.07][chapter]
		if chapter > 0 or b >= 8:
			kick(at, energy)
		if 0 < chapter < 5:
			hat(at + beat / 2, 0.009 if chapter == 4 else 0.014, (-1) ** b * 0.3)
		if b % 2 == 0:
			pluck(
				at, chord[(b // 2) % len(chord)] + 24, [0.04, 0.05, 0.055, 0.055, 0.04, 0.052][chapter], (-1) ** b * 0.4
			)
		if chapter in [2, 3] and b % 4 == 2:
			pluck(at + beat / 2, chord[2] + 24, 0.024, -0.5)
	for n, note in enumerate([chord[1] + 24, chord[2] + 24, chord[4] + 24]):
		pluck(start + n * beat / 2, note, 0.055, n * 0.2 - 0.2, 2.5)

for n, note in enumerate([74, 78, 81, 85]):
	pluck(44 + n * beat / 2, note, 0.055, -0.4 + n * 0.25, 3)

timeline = np.arange(len(audio)) / rate
audio *= (np.clip(timeline / 0.1, 0, 1) * np.clip((length - timeline) / 1.7, 0, 1))[:, None]
peak = np.max(np.abs(audio))
audio *= 0.8 / max(peak, 1e-8)
out = root / "output"
out.mkdir(exist_ok=True)
with wave.open(str(out / "soundtrack.wav"), "wb") as f:
	f.setnchannels(2)
	f.setsampwidth(2)
	f.setframerate(rate)
	f.writeframes((np.clip(audio, -1, 1) * 32767).astype("<i2").tobytes())
subprocess.run(
	[
		"ffmpeg",
		"-y",
		"-v",
		"error",
		"-i",
		str(out / "soundtrack.wav"),
		"-af",
		"loudnorm=I=-18:TP=-1.5:LRA=8",
		"-ar",
		"48000",
		"-c:a",
		"aac",
		"-b:a",
		"192k",
		str(out / "soundtrack.m4a"),
	],
	check=True,
)
print(f"Original score: {length}s / {spec['bpm']} BPM / stereo / {out / 'soundtrack.m4a'}")
