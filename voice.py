"""La voix de Kenobi : entendre (transcription) et parler (synthèse) pendant un appel.

Trois moteurs au choix dans les réglages :
- « local » : Piper et faster-whisper sur cet ordinateur, dans un processus à part (voice_worker.py) installé par
  scripts/installer_voix.py. Aucun son ne quitte la machine.
- « mistral » : Voxtral par l'API de Mistral (transcription et voix françaises).
- « elevenlabs » : Scribe et les voix d'ElevenLabs par leur API.

Le tour d'appel : la phrase est transcrite, le modèle répond en flux, chaque phrase terminée est
dite pendant que la suite s'écrit, dans l'ordre, et tout s'arrête dès que la personne reprend la parole.
"""
import base64
import io
import json
import os
import queue
import re
import secrets
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import wave
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote

import providers
import speech_text

PROJECT_ROOT = Path(__file__).resolve().parent
MAX_AUDIO = 8 * 1024 * 1024
MAX_SECONDS = 60
MIN_SECONDS = 0.25
MAX_SPOKEN = 4000
IDLE_SECONDS = 15 * 60
FILLER_DELAY_MS = 1100
MISTRAL_API = "https://api.mistral.ai/v1"
MISTRAL_STT_MODEL = "voxtral-mini-transcribe-3-0"
MISTRAL_TTS_MODEL = "voxtral-mini-tts-3-0"
ELEVEN_API = "https://api.elevenlabs.io"
ELEVEN_STT_MODEL = "scribe_v2"
# Ce que les reconnaissances vocales écrivent sur du bruit ou du silence : jamais envoyé au tuteur.
NOISE_TRANSCRIPTS = re.compile(
    r"^\W*(sous-titr\w*.*|merci d'avoir regardé.*|abonnez-vous.*|amara\.org.*|\.{2,}|…|\[.*\]|\(.*\))\W*$", re.I)
# Dites dans la voix de Kenobi si la réponse tarde ; courtes, sans promesse.
FILLERS = ("Mmh, une seconde.", "Voyons voir.", "Attends, je regarde.", "Un instant.", "Ok, je réfléchis.",
           "Alors, attends voir.")
GREETINGS_NAMED = ("Oui, {name} ?", "Salut {name}, je t'écoute.", "Je t'écoute, {name}.")
GREETINGS = ("Oui ?", "Salut, je t'écoute.", "Je t'écoute.")
TEST_SENTENCE = "Salut, c'est Kenobi. Si tu m'entends bien, on peut commencer."
_OPENER = urllib.request.build_opener()


class VoiceError(Exception):
    def __init__(self, message, status=502):
        super().__init__(message)
        self.status = status
        self.message = message


def voice_dir():
    override = os.environ.get("KENOBI_VOICE_DIR")
    if override:
        return Path(os.path.expanduser(override))
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "kenobi" / "voice"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "kenobi" / "voice"
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "kenobi" / "voice"


def check_wav(audio):
    """Durée d'un enregistrement WAV mono 16 bits envoyé par la page, ou VoiceError."""
    if not audio or len(audio) > MAX_AUDIO:
        raise VoiceError("Enregistrement vide ou trop volumineux.", 413)
    try:
        with wave.open(io.BytesIO(audio), "rb") as source:
            if source.getsampwidth() != 2 or source.getnchannels() not in (1, 2):
                raise VoiceError("Format audio inattendu.", 400)
            seconds = source.getnframes() / max(1, source.getframerate())
    except (wave.Error, EOFError):
        raise VoiceError("Enregistrement audio illisible.", 400) from None
    if seconds > MAX_SECONDS:
        raise VoiceError("Phrase trop longue (60 secondes au plus).", 413)
    return seconds


def _multipart(fields, file_field, filename, content, mime):
    boundary = secrets.token_hex(24)
    body = b""
    for name, value in fields:
        body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n").encode("utf-8")
    body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{file_field}\"; filename=\"{filename}\"\r\n"
             f"Content-Type: {mime}\r\n\r\n").encode("utf-8") + content + f"\r\n--{boundary}--\r\n".encode("utf-8")
    return body, f"multipart/form-data; boundary={boundary}"


def _send(request, timeout, limit, service):
    try:
        with _OPENER.open(request, timeout=timeout) as response:
            raw = response.read(limit + 1)
    except urllib.error.HTTPError as error:
        error.close()
        # Le code seulement : le corps et les en-têtes peuvent reprendre la demande.
        if error.code in (401, 403):
            raise VoiceError(f"Clé refusée par {service} : vérifie-la dans les réglages de la voix.") from None
        if error.code == 429:
            raise VoiceError(f"Limite ou crédit atteint chez {service}.", 503) from None
        raise VoiceError(f"{service} a répondu par une erreur {error.code}.") from None
    except Exception:
        raise VoiceError(f"{service} est injoignable.", 503) from None
    if len(raw) > limit:
        raise VoiceError(f"Réponse de {service} trop volumineuse.")
    return raw


# ── Moteur local ────────────────────────────────────────────────────────────


class LocalVoice:
    """Piper et faster-whisper dans un processus résident, lancé à la demande et arrêté après un quart d'heure."""

    name = "local"
    label = "Voix locale"

    def __init__(self, root=None, installer=None):
        self.root = Path(root) if root else voice_dir()
        self.installer = Path(installer) if installer else PROJECT_ROOT / "scripts" / "installer_voix.py"
        self.worker = PROJECT_ROOT / "voice_worker.py"
        self.lock = threading.Lock()
        self.process = None
        self.replies = None
        self.last_used = 0.0
        self.install_lock = threading.Lock()
        self.install_state = {"running": False, "step": "", "label": "", "percent": None, "error": ""}

    # Installation

    def manifest(self):
        try:
            data = json.loads((self.root / "manifest.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict):
            return None
        try:
            python = Path(data["python"])
            voice = self.root / data["piper"]["file"]
            whisper_root = self.root / data["whisper"]["root"]
        except (KeyError, TypeError):
            return None
        if not python.is_file() or not voice.is_file() or not whisper_root.is_dir():
            return None
        return {**data, "_python": python, "_voice": voice, "_whisper_root": whisper_root}

    def problem(self):
        if self.install_state["running"]:
            return "La voix locale s'installe : encore un instant."
        if not self.manifest():
            return "La voix locale n'est pas installée : lance l'installation dans les réglages de la voix."
        return None

    def status(self):
        manifest = self.manifest()
        with self.install_lock:
            install = dict(self.install_state)
        return {
            "installed": bool(manifest),
            "running": bool(self.process and self.process.poll() is None),
            "installing": install["running"],
            "step": install["step"], "stepLabel": install["label"], "percent": install["percent"],
            "error": install["error"],
            "voice": manifest["piper"].get("voice") if manifest else "",
            "stt": manifest["whisper"].get("model") if manifest else "",
            "folder": str(self.root),
            "command": f"python3 {self.installer}",
        }

    def install(self):
        """Lance l'installateur dans un processus à part ; l'avancement se lit dans status()."""
        with self.install_lock:
            if self.install_state["running"]:
                return
            self.install_state.update(running=True, step="start", label="Préparation…", percent=1, error="")
        self.close()

        def run():
            error = ""
            try:
                process = subprocess.Popen(
                    [sys.executable, str(self.installer), "--json", "--dir", str(self.root)],
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace")
                for line in process.stdout:
                    try:
                        event = json.loads(line)
                    except ValueError:
                        continue
                    if not isinstance(event, dict):
                        continue
                    if event.get("step") == "error":
                        error = str(event.get("label") or "Installation interrompue.")[:300]
                        continue
                    with self.install_lock:
                        self.install_state.update(step=str(event.get("step") or ""), label=str(event.get("label") or "")[:200],
                                                  percent=event.get("percent"))
                code = process.wait()
                if code != 0 and not error:
                    error = "L'installation de la voix locale s'est arrêtée avant la fin."
            except OSError:
                error = "Impossible de lancer l'installateur de la voix."
            with self.install_lock:
                self.install_state.update(running=False, error=error)
                if not error:
                    self.install_state.update(step="done", label="Voix locale prête", percent=100)

        threading.Thread(target=run, daemon=True).start()

    # Processus résident

    @staticmethod
    def _read(process, replies):
        try:
            for line in process.stdout:
                if len(line) > 65536:
                    break
                replies.put(json.loads(line))
        except (ValueError, OSError):
            pass
        finally:
            replies.put(None)

    def _start(self):
        if self.process and self.process.poll() is None:
            return
        self._stop()
        manifest = self.manifest()
        if not manifest:
            raise VoiceError(self.problem() or "Voix locale indisponible.", 503)
        arguments = [str(manifest["_python"]), str(self.worker), "--voice", str(manifest["_voice"]),
                     "--whisper", str(manifest["whisper"].get("model") or "small"),
                     "--whisper-root", str(manifest["_whisper_root"])]
        self.replies = queue.Queue()
        try:
            self.process = subprocess.Popen(arguments, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                            stderr=subprocess.DEVNULL, text=True, encoding="utf-8")
        except OSError:
            raise VoiceError("Impossible de lancer la voix locale.", 503) from None
        threading.Thread(target=self._read, args=(self.process, self.replies), daemon=True).start()
        ready = self._receive(120)
        if not ready.get("ready"):
            self._stop()
            raise VoiceError("La voix locale n'a pas démarré : relance son installation.", 503)
        threading.Thread(target=self._watch_idle, args=(self.process,), daemon=True).start()

    def _receive(self, timeout):
        try:
            reply = self.replies.get(timeout=timeout)
        except queue.Empty:
            raise VoiceError("La voix locale ne répond pas.", 504) from None
        if not isinstance(reply, dict):
            raise VoiceError("La voix locale s'est arrêtée.", 503)
        return reply

    def _watch_idle(self, process):
        while process.poll() is None:
            time.sleep(30)
            with self.lock:
                if self.process is process and time.monotonic() - self.last_used > IDLE_SECONDS:
                    self._stop()
                    return

    def _stop(self):
        process, self.process = self.process, None
        if not process:
            return
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
        for stream in (process.stdin, process.stdout):
            try:
                if stream:
                    stream.close()
            except OSError:
                pass

    def close(self):
        with self.lock:
            self._stop()

    def _request(self, payload, timeout=90):
        with self.lock:
            try:
                self._start()
                self.last_used = time.monotonic()
                self.process.stdin.write(json.dumps(payload) + "\n")
                self.process.stdin.flush()
                reply = self._receive(timeout)
            except VoiceError:
                self._stop()
                raise
            except (OSError, ValueError):
                self._stop()
                raise VoiceError("La voix locale s'est arrêtée : elle redémarre au prochain essai.", 503) from None
            self.last_used = time.monotonic()
        return reply

    def warm(self):
        self._request({"op": "ping"}, timeout=130)

    def transcribe(self, audio, terms=()):
        with tempfile.TemporaryDirectory(prefix="kenobi-voix-") as folder:
            path = Path(folder) / "phrase.wav"
            path.write_bytes(audio)
            # Whisper suit mieux un bout de phrase qu'une liste sèche : les mots du cours y sont donnés en contexte.
            prompt = ("Séance de tutorat en informatique. Vocabulaire : " + ", ".join(terms) + ".")[:380] if terms else ""
            reply = self._request({"op": "stt", "path": str(path), "language": "fr", "prompt": prompt})
        if not reply.get("ok"):
            raise VoiceError("Transcription locale impossible.")
        return str(reply.get("text") or "").strip()

    def speak(self, text, voice=None):
        with tempfile.TemporaryDirectory(prefix="kenobi-voix-") as folder:
            path = Path(folder) / "phrase.wav"
            reply = self._request({"op": "tts", "text": text[:MAX_SPOKEN], "path": str(path), "speaker": voice or "pierre"})
            if not reply.get("ok"):
                raise VoiceError("Synthèse locale impossible.")
            return path.read_bytes(), "audio/wav"


# ── Voxtral (Mistral) ───────────────────────────────────────────────────────


class MistralVoice:
    name = "mistral"
    label = "Voxtral (Mistral)"

    def __init__(self, key, voice):
        self.key = key
        self.voice = voice

    def problem(self):
        return None if self.key else "Voix Voxtral : il manque la clé d'API Mistral dans les réglages de la voix."

    def transcribe(self, audio, terms=()):
        fields = [("model", MISTRAL_STT_MODEL), ("language", "fr")]
        fields += [("context_bias", term) for term in list(terms)[:24] if re.fullmatch(r"[\w'’.-]{2,40}", term)]
        body, content_type = _multipart(fields, "file", "voix.wav", audio, "audio/wav")
        request = urllib.request.Request(MISTRAL_API + "/audio/transcriptions", body,
                                         {"Content-Type": content_type, "Authorization": "Bearer " + self.key})
        raw = _send(request, 60, 65536, "Mistral")
        try:
            text = json.loads(raw).get("text")
        except (ValueError, AttributeError):
            raise VoiceError("Réponse de transcription Voxtral illisible.") from None
        return text.strip() if isinstance(text, str) else ""

    def speak(self, text, voice=None):
        payload = {"model": MISTRAL_TTS_MODEL, "input": text[:MAX_SPOKEN], "voice_id": voice or self.voice,
                   "response_format": "wav"}
        request = urllib.request.Request(MISTRAL_API + "/audio/speech", json.dumps(payload).encode("utf-8"),
                                         {"Content-Type": "application/json", "Authorization": "Bearer " + self.key})
        raw = _send(request, 60, 64 * 1024 * 1024, "Mistral")
        try:
            audio = base64.b64decode(json.loads(raw).get("audio_data", ""), validate=True)
        except (ValueError, TypeError, AttributeError):
            raise VoiceError("Réponse de synthèse Voxtral illisible.") from None
        if len(audio) < 44 or not audio.startswith(b"RIFF"):
            raise VoiceError("Audio Voxtral invalide.")
        return audio, "audio/wav"


# ── ElevenLabs ──────────────────────────────────────────────────────────────


class ElevenLabsVoice:
    name = "elevenlabs"
    label = "ElevenLabs"

    def __init__(self, key, voice, model):
        self.key = key
        self.voice = voice
        self.model = model

    def problem(self):
        if not self.key:
            return "Voix ElevenLabs : il manque la clé d'API dans les réglages de la voix."
        if not self.voice:
            return "Voix ElevenLabs : choisis une voix dans les réglages."
        return None

    def transcribe(self, audio, terms=()):
        fields = [("model_id", ELEVEN_STT_MODEL), ("language_code", "fr"), ("tag_audio_events", "false"),
                  ("diarize", "false")]
        body, content_type = _multipart(fields, "file", "voix.wav", audio, "audio/wav")
        request = urllib.request.Request(ELEVEN_API + "/v1/speech-to-text", body,
                                         {"Content-Type": content_type, "xi-api-key": self.key})
        raw = _send(request, 60, 1024 * 1024, "ElevenLabs")
        try:
            text = json.loads(raw).get("text")
        except (ValueError, AttributeError):
            raise VoiceError("Réponse de transcription ElevenLabs illisible.") from None
        return text.strip() if isinstance(text, str) else ""

    def speak(self, text, voice=None):
        payload = {"text": text[:MAX_SPOKEN], "model_id": self.model}
        if re.search(r"flash|turbo", self.model):
            payload["language_code"] = "fr"
        url = f"{ELEVEN_API}/v1/text-to-speech/{quote(voice or self.voice, safe='')}?output_format=mp3_44100_128"
        request = urllib.request.Request(url, json.dumps(payload).encode("utf-8"),
                                         {"Content-Type": "application/json", "xi-api-key": self.key,
                                          "Accept": "audio/mpeg"})
        audio = _send(request, 60, 32 * 1024 * 1024, "ElevenLabs")
        if len(audio) < 200:
            raise VoiceError("Audio ElevenLabs invalide.")
        return audio, "audio/mpeg"

    def voices(self):
        """Voix du compte : [{id, name, language, gender, category}], les françaises d'abord."""
        raw = None
        for path in ("/v2/voices?page_size=100", "/v1/voices"):
            request = urllib.request.Request(ELEVEN_API + path, headers={"xi-api-key": self.key})
            try:
                raw = _send(request, 30, 8 * 1024 * 1024, "ElevenLabs")
                break
            except VoiceError as error:
                if "Clé refusée" in error.message or path == "/v1/voices":
                    raise
        try:
            items = json.loads(raw).get("voices") or []
        except (ValueError, AttributeError):
            raise VoiceError("Liste des voix ElevenLabs illisible.") from None
        voices = []
        for item in items:
            if not isinstance(item, dict) or not item.get("voice_id"):
                continue
            labels = item.get("labels") if isinstance(item.get("labels"), dict) else {}
            languages = [str(entry.get("language") or "") for entry in item.get("verified_languages") or []
                         if isinstance(entry, dict)]
            language = str(labels.get("language") or (languages[0] if languages else ""))
            voices.append({"id": str(item["voice_id"]), "name": str(item.get("name") or item["voice_id"])[:60],
                           "language": language[:12], "french": language.startswith("fr") or "fr" in languages,
                           "gender": str(labels.get("gender") or "")[:12], "category": str(item.get("category") or "")[:20]})
        voices.sort(key=lambda voice: (not voice["french"], voice["name"].lower()))
        return voices[:200]


# ── Tour d'appel ────────────────────────────────────────────────────────────


class _Speaker:
    """Synthétise les morceaux en file, dans l'ordre, sur un seul fil, et publie chacun dès qu'il est prêt.

    Après un échec de synthèse ou un arrêt, plus rien n'est dit : ni trou, ni morceau dans le désordre.
    """

    def __init__(self, synth, publish, cancel):
        self.synth, self.publish, self.cancel = synth, publish, cancel
        self.queue = queue.Queue()
        self.count, self.failed, self.error = 0, False, ""
        self.first_ready = None
        self.started = time.monotonic()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def put(self, text):
        self.queue.put(text)

    def _run(self):
        while True:
            text = self.queue.get()
            if text is None:
                return
            if self.failed or self.cancel.is_set() or not text.strip():
                continue
            try:
                audio, mime = self.synth(text)
            except Exception as error:
                self.failed = True
                self.error = getattr(error, "message", "") or "Voix indisponible."
                continue
            if self.cancel.is_set():
                continue
            if self.count == 0:
                self.first_ready = round(time.monotonic() - self.started, 2)
            self.publish("segment", index=self.count, text=text, mime=mime,
                         audio=base64.b64encode(audio).decode("ascii"))
            self.count += 1

    def close(self):
        """Attend que tout ce qui est en file soit dit (ou abandonné)."""
        self.queue.put(None)
        self.thread.join()


class VoiceManager:
    """Le moteur choisi dans les réglages, les phrases d'accueil et d'attente, et le tour d'appel."""

    def __init__(self, settings, root=None, installer=None):
        self.settings = settings
        self.local = LocalVoice(root, installer)
        self._phrases = {}
        self._phrases_lock = threading.Lock()

    def engine(self, config=None):
        config = config or self.settings.voice()
        if config["engine"] == "mistral":
            return MistralVoice(config["mistral"]["apiKey"], config["mistral"]["voice"]), config["mistral"]["voice"]
        if config["engine"] == "elevenlabs":
            eleven = config["elevenlabs"]
            return ElevenLabsVoice(eleven["apiKey"], eleven["voice"], eleven["model"]), eleven["voice"]
        return self.local, config["local"]["voice"]

    def status(self):
        config = self.settings.voice()
        engine, _voice = self.engine(config)
        problem = engine.problem()
        return {"engine": config["engine"], "label": engine.label, "ready": problem is None, "problem": problem or "",
                "local": self.local.status(), "fillers": config["fillers"], "fillerDelayMs": FILLER_DELAY_MS}

    def _spoken(self, text, config):
        return speech_text.spoken_fr(text) if config["swissNumbers"] else text

    def speak(self, text, config=None):
        config = config or self.settings.voice()
        engine, voice = self.engine(config)
        problem = engine.problem()
        if problem:
            raise VoiceError(problem, 503)
        return engine.speak(self._spoken(text, config), voice)

    def test(self):
        audio, mime = self.speak(TEST_SENTENCE)
        return {"audio": base64.b64encode(audio).decode("ascii"), "mime": mime, "text": TEST_SENTENCE}

    def voices(self, key=None):
        config = self.settings.voice()["elevenlabs"]
        key = key or config["apiKey"]
        if not key:
            raise VoiceError("Clé ElevenLabs manquante.", 400)
        return ElevenLabsVoice(key, config["voice"], config["model"]).voices()

    def phrases(self, name=""):
        """Accueils et phrases d'attente déjà dits dans la voix choisie ; gardés en mémoire, jamais sur disque."""
        config = self.settings.voice()
        engine, voice = self.engine(config)
        if engine.problem():
            return {"greetings": [], "fillers": []}
        name = re.sub(r"[^\w' -]", "", name or "").strip()[:30]
        key = (config["engine"], voice, name, config["swissNumbers"], config["fillers"])
        with self._phrases_lock:
            cached = self._phrases.get(key)
        if cached and (cached["greetings"] or time.monotonic() - cached["at"] < 60):
            return {"greetings": cached["greetings"], "fillers": cached["fillers"]}
        greetings = [text.format(name=name) for text in GREETINGS_NAMED] if name else list(GREETINGS)
        texts = [("greetings", text) for text in greetings]
        texts += [("fillers", text) for text in FILLERS] if config["fillers"] else []

        def one(item):
            kind, text = item
            audio, mime = engine.speak(self._spoken(text, config), voice)
            return kind, {"text": text, "mime": mime, "audio": base64.b64encode(audio).decode("ascii")}

        built = {"greetings": [], "fillers": [], "at": time.monotonic()}
        try:
            # Le moteur local traite une phrase à la fois ; les API en acceptent plusieurs de front.
            with ThreadPoolExecutor(max_workers=1 if engine is self.local else 4) as pool:
                for kind, entry in pool.map(one, texts):
                    built[kind].append(entry)
        except Exception:
            built = {"greetings": [], "fillers": [], "at": time.monotonic()}  # jamais une banque à moitié dite
        with self._phrases_lock:
            self._phrases = {key: built}
        return {"greetings": built["greetings"], "fillers": built["fillers"]}

    def turn(self, start_reply, *, audio=None, text=None, carry="", terms=()):
        """Un tour d'appel, en évènements : stage, transcript, delta, segment, puis done, empty ou error.

        `start_reply(texte, cancel)` génère la réponse du tuteur. Fermer ce générateur (la page a coupé, ou la
        personne a repris la parole) arrête le modèle et la voix.
        """
        config = self.settings.voice()
        engine, voice = self.engine(config)
        out = queue.Queue()
        cancel = providers.Cancel()

        def publish(kind, **data):
            out.put((kind, data))

        def work():
            speaker = None
            started = time.monotonic()
            try:
                problem = engine.problem()
                heard, stt_ms = text, None
                if audio is not None:
                    if problem:
                        raise VoiceError(problem, 503)
                    check_wav(audio)
                    publish("stage", name="transcription")
                    begin = time.monotonic()
                    heard = engine.transcribe(audio, terms)
                    stt_ms = round((time.monotonic() - begin) * 1000)
                    if not heard or NOISE_TRANSCRIPTS.match(heard):
                        publish("empty")
                        return
                heard = (heard or "").strip()
                if not heard:
                    publish("empty")
                    return
                if cancel.is_set():
                    return
                full = f"{carry.strip()} {heard}".strip() if carry else heard
                publish("transcript", text=full, heard=heard, sttMs=stt_ms)
                publish("stage", name="answer")
                if problem:
                    # Tour écrit (un Run pendant l'appel) sans voix disponible : la réponse reste à l'écran.
                    speaker = None
                else:
                    speaker = _Speaker(lambda piece: engine.speak(self._spoken(piece, config), voice), publish, cancel)
                segmenter = speech_text.StreamSegmenter()
                reply, result = [], {}
                for kind, data in start_reply(full, cancel):
                    if kind == "delta":
                        reply.append(data["text"])
                        publish("delta", text=data["text"])
                        if speaker:
                            for piece in segmenter.feed(data["text"]):
                                speaker.put(piece)
                    elif kind == "done":
                        result = data
                if speaker:
                    for piece in segmenter.finish():
                        speaker.put(piece)
                    speaker.close()
                if cancel.is_set():
                    return
                publish("done", reply="".join(reply), segments=speaker.count if speaker else 0,
                        voiceError=(speaker.error if speaker and speaker.failed else problem or ""),
                        firstAudio=speaker.first_ready if speaker else None, sttMs=stt_ms,
                        total=round(time.monotonic() - started, 2), **result)
            except Exception as error:
                if not cancel.is_set():
                    publish("error", message=getattr(error, "message", None) or "Erreur pendant l'appel.",
                            status=getattr(error, "status", 500))
            finally:
                cancel.set()
                if speaker and speaker.thread.is_alive():
                    speaker.close()
                out.put((None, None))

        threading.Thread(target=work, daemon=True).start()
        try:
            while True:
                kind, data = out.get()
                if kind is None:
                    return
                yield kind, data
        finally:
            cancel.set()

    def close(self):
        self.local.close()
