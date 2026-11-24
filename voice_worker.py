#!/usr/bin/env python3
"""Processus de la voix locale : transcription (faster-whisper) et synthèse (Piper), sur cet ordinateur.

Lancé par voice.py avec le Python de l'environnement de la voix (scripts/installer_voix.py). Dialogue par lignes
JSON sur l'entrée et la sortie standard : une demande par ligne, une réponse par ligne. Rien n'est gardé ni
envoyé ailleurs : ni son, ni texte ; les modèles sont lus sur le disque, jamais téléchargés ici.

  {"op": "tts", "text": "...", "path": "/tmp/x.wav", "speaker": "pierre"}  → {"ok": true, "seconds": 1.2, ...}
  {"op": "stt", "path": "/tmp/y.wav", "language": "fr", "prompt": "..."}   → {"ok": true, "text": "..."}

La mise bout à bout des phrases : silences de bord rognés, une seule normalisation.
"""
import argparse
import contextlib
import json
import os
import sys
import time
import wave
from pathlib import Path

import numpy as np

MAX_TEXT = 4000
MAX_SECONDS = 120
EDGE_MS = 50
SILENCE_FRACTION = 0.02
PEAK_TARGET = 30000
STT_RATE = 16000


def _samples(data):
    return np.frombuffer(data, dtype="<i2").astype(np.int32)


def _bytes(samples):
    return np.clip(np.rint(samples), -32768, 32767).astype("<i2").tobytes()


def pcm_trim(data, rate, margin_ms=EDGE_MS):
    """Garde le son utile et au plus margin_ms de silence à chaque bord."""
    samples = _samples(data)
    if not samples.size or not samples.any():
        return b""
    loud = np.abs(samples) > max(1, int(np.max(np.abs(samples)) * SILENCE_FRACTION))
    if not loud.any():
        return b""
    first, last = int(np.argmax(loud)), len(loud) - 1 - int(np.argmax(loud[::-1]))
    margin = rate * margin_ms // 1000
    return _bytes(samples[max(first - margin, 0):min(last + margin, len(samples) - 1) + 1])


def pcm_normalize(data, target=PEAK_TARGET):
    samples = _samples(data)
    peak = int(np.max(np.abs(samples))) if samples.size else 0
    return data if peak == 0 else _bytes(samples * (target / peak))


def read_wav(path):
    """Échantillons mono en float32 à 16 kHz, comme Whisper les attend."""
    with wave.open(str(path), "rb") as source:
        channels, width, rate, frames = source.getnchannels(), source.getsampwidth(), source.getframerate(), source.getnframes()
        if width != 2 or channels not in (1, 2) or rate < 8000 or frames <= 0 or frames / rate > MAX_SECONDS:
            raise ValueError("unsupported_audio")
        raw = source.readframes(frames)
    audio = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    if channels == 2:
        audio = audio.reshape(-1, 2).mean(axis=1)
    if rate != STT_RATE:
        target = int(round(len(audio) * STT_RATE / rate))
        audio = np.interp(np.linspace(0, len(audio) - 1, target), np.arange(len(audio)), audio).astype(np.float32)
    return audio


def main():
    started = time.perf_counter()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--voice", required=True, type=Path, help="fichier .onnx de la voix Piper")
    parser.add_argument("--whisper", required=True, help="dossier ou nom du modèle faster-whisper")
    parser.add_argument("--whisper-root", type=Path, help="dossier des modèles Whisper téléchargés")
    parser.add_argument("--threads", type=int, default=max(2, min(8, (os.cpu_count() or 4) // 2)))
    args = parser.parse_args()

    # Les bibliothèques écrivent parfois sur la sortie standard : elle est réservée au dialogue.
    with contextlib.redirect_stdout(sys.stderr):
        import piper
        from piper import SynthesisConfig
        from faster_whisper import WhisperModel
        voice = piper.PiperVoice.load(str(args.voice))
        speakers = dict(voice.config.speaker_id_map or {})
        whisper = WhisperModel(args.whisper, device="cpu", compute_type="int8", cpu_threads=args.threads,
                               download_root=str(args.whisper_root) if args.whisper_root else None,
                               local_files_only=True)
        for _chunk in voice.synthesize("Bonjour.", syn_config=SynthesisConfig(
                speaker_id=sorted(speakers.values())[0] if speakers else None)):
            break

    def speak(request):
        text = " ".join(str(request.get("text") or "").split())
        if not text or len(text) > MAX_TEXT:
            raise ValueError("invalid_text")
        speaker = request.get("speaker")
        speaker_id = speakers.get(speaker) if speakers else None
        if speakers and speaker_id is None:
            speaker_id = sorted(speakers.values())[0]
        scale = float(request.get("length_scale") or 1.0)
        config = SynthesisConfig(speaker_id=speaker_id, length_scale=max(0.8, min(1.25, scale)), normalize_audio=False)
        begin = time.perf_counter()
        rate, pieces = None, []
        with contextlib.redirect_stdout(sys.stderr):
            for chunk in voice.synthesize(text, syn_config=config):
                rate = chunk.sample_rate
                pieces.append(pcm_trim(chunk.audio_int16_bytes, chunk.sample_rate))
        audio = pcm_normalize(b"".join(piece for piece in pieces if piece))
        if not audio or not rate:
            raise ValueError("empty_synthesis")
        with wave.open(str(request["path"]), "wb") as out:
            out.setnchannels(1)
            out.setsampwidth(2)
            out.setframerate(rate)
            out.writeframes(audio)
        return {"sample_rate": rate, "seconds": round(len(audio) / (2 * rate), 3),
                "took": round(time.perf_counter() - begin, 3)}

    def transcribe(request):
        begin = time.perf_counter()
        audio = read_wav(request["path"])
        language = str(request.get("language") or "fr")
        prompt = str(request.get("prompt") or "")[:400] or None
        with contextlib.redirect_stdout(sys.stderr):
            segments, _info = whisper.transcribe(
                audio, language=None if language == "auto" else language, beam_size=5, temperature=0.0,
                condition_on_previous_text=False, vad_filter=False, without_timestamps=True, initial_prompt=prompt)
            kept = []
            for segment in segments:
                # Un passage que le modèle juge lui-même sans parole n'est pas une phrase.
                if segment.no_speech_prob > 0.6 and segment.avg_logprob < -1.0:
                    continue
                kept.append(segment.text.strip())
        return {"text": " ".join(part for part in kept if part).strip(),
                "audio_seconds": round(len(audio) / STT_RATE, 2), "took": round(time.perf_counter() - begin, 3)}

    print(json.dumps({"ready": True, "speakers": speakers, "sample_rate": voice.config.sample_rate,
                      "threads": args.threads, "load_seconds": round(time.perf_counter() - started, 2)}), flush=True)
    for line in sys.stdin:
        try:
            request = json.loads(line)
            operation = request.get("op")
            if operation == "tts":
                reply = {"ok": True, **speak(request)}
            elif operation == "stt":
                reply = {"ok": True, **transcribe(request)}
            elif operation == "ping":
                reply = {"ok": True}
            else:
                reply = {"ok": False, "error": "unknown_op"}
        except Exception as error:  # la demande est refusée, le processus reste en vie
            reply = {"ok": False, "error": error.args[0] if isinstance(error, ValueError) and error.args else "failed"}
        print(json.dumps(reply), flush=True)


if __name__ == "__main__":
    main()
