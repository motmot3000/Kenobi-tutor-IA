"""Réglages privés de Kenobi : modèle d'IA, voix et profil de l'apprenant.

Un fichier JSON en 0600 dans le dossier de configuration du compte, jamais dans le dépôt ni dans l'état du
parcours. Une clé d'API n'en sort que vers le service qu'elle ouvre : le navigateur apprend seulement qu'une clé
est enregistrée. Une clé reste liée à l'adresse pour laquelle elle a été saisie : changer de fournisseur ou
d'adresse sans redonner la clé l'efface, pour qu'elle ne parte jamais ailleurs.
"""
import json
import os
import re
import stat
import sys
import tempfile
import threading
from pathlib import Path
from urllib.parse import urlsplit

VERSION = 1

# Fournisseurs proposés. `kind` : protocole (« openai » = /chat/completions, « anthropic » = /messages).
# Aucun nom de modèle n'est supposé : la liste vient du fournisseur une fois la clé donnée.
PROVIDERS = (
    {"id": "mistral", "label": "Mistral", "kind": "openai", "baseUrl": "https://api.mistral.ai/v1",
     "env": "MISTRAL_API_KEY", "keyUrl": "https://console.mistral.ai/api-keys"},
    {"id": "openai", "label": "OpenAI", "kind": "openai", "baseUrl": "https://api.openai.com/v1",
     "env": "OPENAI_API_KEY", "keyUrl": "https://platform.openai.com/api-keys"},
    {"id": "anthropic", "label": "Anthropic (Claude)", "kind": "anthropic", "baseUrl": "https://api.anthropic.com/v1",
     "env": "ANTHROPIC_API_KEY", "keyUrl": "https://console.anthropic.com/settings/keys"},
    {"id": "google", "label": "Google (Gemini)", "kind": "openai",
     "baseUrl": "https://generativelanguage.googleapis.com/v1beta/openai",
     "env": "GEMINI_API_KEY", "keyUrl": "https://aistudio.google.com/apikey"},
    {"id": "openrouter", "label": "OpenRouter", "kind": "openai", "baseUrl": "https://openrouter.ai/api/v1",
     "env": "OPENROUTER_API_KEY", "keyUrl": "https://openrouter.ai/keys"},
    {"id": "deepseek", "label": "DeepSeek", "kind": "openai", "baseUrl": "https://api.deepseek.com/v1",
     "env": "DEEPSEEK_API_KEY", "keyUrl": "https://platform.deepseek.com/api_keys"},
    {"id": "groq", "label": "Groq", "kind": "openai", "baseUrl": "https://api.groq.com/openai/v1",
     "env": "GROQ_API_KEY", "keyUrl": "https://console.groq.com/keys"},
    {"id": "ollama", "label": "Ollama (sur cet ordinateur)", "kind": "openai", "baseUrl": "http://127.0.0.1:11434/v1",
     "keyless": True, "editableUrl": True},
    {"id": "custom", "label": "Autre service compatible OpenAI", "kind": "openai", "baseUrl": "",
     "keyOptional": True, "editableUrl": True},
)
PROVIDER_IDS = {item["id"]: item for item in PROVIDERS}
REASONING = ("none", "auto", "low", "medium", "high")
VOICE_ENGINES = ("local", "mistral", "elevenlabs")
LOCAL_VOICES = ("pierre", "jessica")
# Voix françaises préréglées de Voxtral (Mistral), relevées le 2026-10-29.
MISTRAL_VOICES = (("voice_589", "Léa"), ("voice_3141", "Camille"), ("voice_3137", "Manon"), ("voice_592", "Marie"))
ELEVENLABS_MODEL = "eleven_flash_v2_5"

_MODEL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,119}")
_VOICE_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")
_KEY_MAX = 512
_lock = threading.RLock()


class SettingsError(ValueError):
    """Réglage refusé : le message est destiné à la personne qui règle Kenobi."""


def config_dir():
    override = os.environ.get("KENOBI_CONFIG_DIR")
    if override:
        return Path(os.path.expanduser(override))
    if os.name == "nt":
        return Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / "kenobi"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "kenobi"
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "kenobi"


def _clean_text(value, limit):
    text = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", str(value or "")).strip()
    return text[:limit]


def _clean_key(value):
    key = str(value or "").strip()
    if not key:
        return ""
    if len(key) > _KEY_MAX or any(character.isspace() or ord(character) < 33 for character in key):
        raise SettingsError("Clé d'API illisible : colle-la sans espace ni retour à la ligne.")
    return key


def normalize_base_url(value):
    """Adresse d'une API : http(s)://hôte[:port][/chemin], sans identifiants ni paramètres."""
    text = str(value or "").strip().rstrip("/")
    if not text:
        return ""
    parts = urlsplit(text)
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.password:
        raise SettingsError("Adresse de l'API invalide : attendu http(s)://hôte[:port]/v1.")
    if parts.query or parts.fragment or len(text) > 200:
        raise SettingsError("Adresse de l'API invalide : sans paramètres ni ancre.")
    return text


def _defaults():
    return {
        "version": VERSION,
        "learner": {"name": "", "about": ""},
        "ai": {"provider": "", "baseUrl": "", "model": "", "apiKey": "", "reasoning": "none"},
        "voice": {
            "engine": "local",
            "local": {"voice": LOCAL_VOICES[0]},
            "mistral": {"apiKey": "", "voice": MISTRAL_VOICES[0][0]},
            "elevenlabs": {"apiKey": "", "voice": "", "voiceName": "", "model": ELEVENLABS_MODEL},
            "swissNumbers": False,
            "fillers": True,
        },
    }


def _merge(base, extra):
    for key, value in (extra or {}).items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _merge(base[key], value)
        elif key in base and isinstance(value, type(base[key])):
            base[key] = value
    return base


class Settings:
    """Lecture, validation et écriture des réglages ; rien d'autre ne touche au fichier."""

    def __init__(self, directory=None):
        self.directory = Path(directory) if directory else config_dir()
        self.path = self.directory / "settings.json"

    # Fichier

    def load(self):
        data = _defaults()
        with _lock:
            try:
                info = os.lstat(self.path)
            except OSError:
                return data
            # Un fichier que d'autres comptes peuvent lire, ou un lien, n'est pas un coffre : on l'ignore.
            if not stat.S_ISREG(info.st_mode):
                return data
            if os.name != "nt" and (info.st_uid != os.getuid() or info.st_mode & 0o077):
                return data
            try:
                stored = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return data
        return _merge(data, stored if isinstance(stored, dict) else {})

    def save(self, data):
        payload = (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        with _lock:
            self.directory.mkdir(parents=True, exist_ok=True)
            if os.name != "nt":
                os.chmod(self.directory, 0o700)
            handle, temporary = tempfile.mkstemp(dir=self.directory, prefix=".settings.")
            try:
                with os.fdopen(handle, "wb") as stream:
                    if os.name != "nt":
                        os.fchmod(stream.fileno(), 0o600)
                    stream.write(payload)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, self.path)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)

    # Modèle d'IA

    def ai(self, data=None):
        """Réglage du modèle prêt à l'emploi : la clé enregistrée, sinon celle de l'environnement."""
        ai = (data or self.load())["ai"]
        preset = PROVIDER_IDS.get(ai["provider"])
        if not preset:
            return {"provider": "", "label": "", "kind": "openai", "baseUrl": "", "model": "", "apiKey": "",
                    "keySource": "", "reasoning": "none", "configured": False, "keyless": False}
        key, source = ai["apiKey"], "saved" if ai["apiKey"] else ""
        if not key and preset.get("env") and os.environ.get(preset["env"]):
            key, source = os.environ[preset["env"]].strip(), "env"
        base = (ai["baseUrl"] or preset["baseUrl"]) if preset.get("editableUrl") else preset["baseUrl"]
        keyless = bool(preset.get("keyless") or preset.get("keyOptional"))
        return {
            "provider": preset["id"], "label": preset["label"], "kind": preset["kind"], "baseUrl": base or "",
            "model": ai["model"], "apiKey": key, "keySource": source, "reasoning": ai["reasoning"],
            "keyless": keyless,
            "configured": bool(base and ai["model"] and (key or keyless)),
        }

    def _update_ai(self, current, patch):
        provider = str(patch.get("provider", current["provider"]) or "")
        if provider and provider not in PROVIDER_IDS:
            raise SettingsError("Fournisseur inconnu.")
        preset = PROVIDER_IDS.get(provider, {})
        if preset.get("editableUrl"):
            base = normalize_base_url(patch.get("baseUrl", current["baseUrl"])) or preset.get("baseUrl", "")
        else:
            base = preset.get("baseUrl", "")
        previous_base = current["baseUrl"] or PROVIDER_IDS.get(current["provider"], {}).get("baseUrl", "")
        key = current["apiKey"]
        if provider != current["provider"] or base != previous_base:
            key = ""
        if patch.get("clearKey"):
            key = ""
        if patch.get("apiKey"):
            key = _clean_key(patch["apiKey"])
        model = str(patch.get("model", current["model"]) or "").strip()
        if provider != current["provider"] and "model" not in patch:
            model = ""
        if model and not _MODEL_RE.fullmatch(model):
            raise SettingsError("Nom de modèle invalide.")
        reasoning = str(patch.get("reasoning", current["reasoning"]) or "none")
        if reasoning not in REASONING:
            raise SettingsError("Réglage de raisonnement inconnu.")
        current.update(provider=provider, baseUrl=base if preset.get("editableUrl") else "", model=model,
                       apiKey=key, reasoning=reasoning)

    # Voix

    def voice(self, data=None):
        """Réglage de la voix prêt à l'emploi. Voxtral peut reprendre la clé Mistral du modèle : même service."""
        data = data or self.load()
        voice = data["voice"]
        engine = voice["engine"] if voice["engine"] in VOICE_ENGINES else "local"
        mistral_key = voice["mistral"]["apiKey"]
        if not mistral_key:
            ai = self.ai(data)
            if ai["provider"] == "mistral" and ai["apiKey"]:
                mistral_key = ai["apiKey"]
            elif os.environ.get("MISTRAL_API_KEY"):
                mistral_key = os.environ["MISTRAL_API_KEY"].strip()
        eleven_key = voice["elevenlabs"]["apiKey"] or os.environ.get("ELEVENLABS_API_KEY", "").strip()
        return {
            "engine": engine,
            "local": {"voice": voice["local"]["voice"] if voice["local"]["voice"] in LOCAL_VOICES else LOCAL_VOICES[0]},
            "mistral": {"apiKey": mistral_key, "voice": voice["mistral"]["voice"] or MISTRAL_VOICES[0][0]},
            "elevenlabs": {"apiKey": eleven_key, "voice": voice["elevenlabs"]["voice"],
                           "model": voice["elevenlabs"]["model"] or ELEVENLABS_MODEL},
            "swissNumbers": bool(voice["swissNumbers"]),
            "fillers": bool(voice["fillers"]),
        }

    def _update_voice(self, current, patch):
        engine = str(patch.get("engine", current["engine"]) or "local")
        if engine not in VOICE_ENGINES:
            raise SettingsError("Moteur de voix inconnu.")
        current["engine"] = engine
        for flag in ("swissNumbers", "fillers"):
            if flag in patch:
                current[flag] = bool(patch[flag])
        local = patch.get("local") if isinstance(patch.get("local"), dict) else {}
        if "voice" in local:
            if local["voice"] not in LOCAL_VOICES:
                raise SettingsError("Voix locale inconnue.")
            current["local"]["voice"] = local["voice"]
        for name in ("mistral", "elevenlabs"):
            part = patch.get(name) if isinstance(patch.get(name), dict) else {}
            if part.get("clearKey"):
                current[name]["apiKey"] = ""
            if part.get("apiKey"):
                current[name]["apiKey"] = _clean_key(part["apiKey"])
            if "voice" in part:
                voice = str(part["voice"] or "").strip()
                if voice and not _VOICE_RE.fullmatch(voice):
                    raise SettingsError("Identifiant de voix invalide.")
                current[name]["voice"] = voice
        eleven = patch.get("elevenlabs") if isinstance(patch.get("elevenlabs"), dict) else {}
        if "voiceName" in eleven:
            current["elevenlabs"]["voiceName"] = _clean_text(eleven["voiceName"], 60)
        if "model" in eleven:
            model = str(eleven["model"] or "").strip() or ELEVENLABS_MODEL
            if not _MODEL_RE.fullmatch(model):
                raise SettingsError("Modèle ElevenLabs invalide.")
            current["elevenlabs"]["model"] = model

    # Mise à jour et vue publique

    def update(self, patch):
        if not isinstance(patch, dict):
            raise SettingsError("Réglages illisibles.")
        with _lock:
            data = self.load()
            learner = patch.get("learner")
            if isinstance(learner, dict):
                if "name" in learner:
                    data["learner"]["name"] = _clean_text(learner["name"], 40)
                if "about" in learner:
                    data["learner"]["about"] = _clean_text(learner["about"], 600)
            if isinstance(patch.get("ai"), dict):
                self._update_ai(data["ai"], patch["ai"])
            if isinstance(patch.get("voice"), dict):
                self._update_voice(data["voice"], patch["voice"])
            self.save(data)
        return data

    def public(self, data=None):
        """Ce que le navigateur peut voir : jamais une clé, seulement sa présence."""
        data = data or self.load()
        ai = self.ai(data)
        voice = self.voice(data)
        stored = data["voice"]
        return {
            "learner": dict(data["learner"]),
            "ai": {
                "provider": ai["provider"], "label": ai["label"], "baseUrl": ai["baseUrl"], "model": ai["model"],
                "reasoning": ai["reasoning"], "hasKey": bool(ai["apiKey"]), "keySource": ai["keySource"],
                "configured": ai["configured"],
            },
            "voice": {
                "engine": voice["engine"],
                "local": voice["local"],
                "mistral": {"hasKey": bool(voice["mistral"]["apiKey"]), "ownKey": bool(stored["mistral"]["apiKey"]),
                            "voice": voice["mistral"]["voice"]},
                "elevenlabs": {"hasKey": bool(voice["elevenlabs"]["apiKey"]), "voice": voice["elevenlabs"]["voice"],
                               "voiceName": stored["elevenlabs"]["voiceName"], "model": voice["elevenlabs"]["model"]},
                "swissNumbers": voice["swissNumbers"],
                "fillers": voice["fillers"],
            },
            "providers": [
                {"id": item["id"], "label": item["label"], "baseUrl": item["baseUrl"], "keyUrl": item.get("keyUrl", ""),
                 "keyless": bool(item.get("keyless")), "keyOptional": bool(item.get("keyOptional")),
                 "editableUrl": bool(item.get("editableUrl")),
                 "envKey": bool(item.get("env") and os.environ.get(item["env"]))}
                for item in PROVIDERS
            ],
            "mistralVoices": [{"id": voice_id, "label": label} for voice_id, label in MISTRAL_VOICES],
            "localVoices": [{"id": "pierre", "label": "Pierre"}, {"id": "jessica", "label": "Jessica"}],
        }

    def learner_name(self, data=None):
        return (data or self.load())["learner"]["name"]
