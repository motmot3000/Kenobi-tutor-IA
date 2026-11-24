"""Passerelle vocale : une ligne d'appel extérieure parle à Kenobi comme à un agent.

Une application d'appel (une ligne vocale pour téléphone, par exemple) tourne sur le même ordinateur. Elle envoie ce qu'elle a entendu à `POST /v1/chat/completions`
(protocole compatible OpenAI, en flux SSE, modèle « kenobi ») avec un jeton dans l'en-tête Authorization, et dit la
réponse avec sa propre voix. Kenobi répond dans la séance en cours du cours actif, avec le même contexte que dans
l'application, mais en appel sans écran (`tutor.PHONE_RULES`) ; le tour s'ajoute à la discussion de cette séance.

La passerelle est fermée tant que son jeton n'existe pas :

    python3 gateway.py --creer-jeton      # crée <dossier des réglages>/gateway.token (0600) et dit où il est
    python3 gateway.py --etat             # dit si la passerelle est ouverte, sans afficher le jeton

Seule une requête de cet ordinateur (client et en-tête Host en boucle locale) avec le bon jeton est servie.
"""
import argparse
import hmac
import json
import os
import re
import secrets
import stat
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import providers
import settings as settings_module
import speech_text
import tutor

MODEL = "kenobi"
MAX_BODY = 512 * 1024
MAX_HEARD = 4000
TOKEN_NAME = "gateway.token"
# Consignes que l'application d'appel place avant la phrase entendue : Kenobi a les siennes (tutor.PHONE_RULES).
_LEADING_RULES = re.compile(r"^(?:\s*\[[^\]\n]*\]\s*\n)+")


class GatewayError(Exception):
    def __init__(self, status, message, kind="invalid_request_error"):
        super().__init__(message)
        self.status = status
        self.message = message
        self.kind = kind


def token_path():
    override = os.environ.get("KENOBI_GATEWAY_TOKEN_FILE")
    return Path(os.path.expanduser(override)) if override else settings_module.config_dir() / TOKEN_NAME


def read_token(path=None):
    """Le jeton, s'il existe dans un fichier privé (0600, même propriétaire) ; sinon None : passerelle fermée."""
    path = Path(path) if path else token_path()
    try:
        info = os.lstat(path)
    except OSError:
        return None
    if not stat.S_ISREG(info.st_mode):
        return None
    if os.name != "nt" and (info.st_uid != os.getuid() or info.st_mode & 0o077):
        return None
    try:
        token = path.read_text(encoding="ascii").strip()
    except (OSError, UnicodeDecodeError):
        return None
    return token if re.fullmatch(r"[A-Za-z0-9_-]{24,128}", token) else None


def create_token(path=None):
    """Crée le jeton s'il n'existe pas encore (0600) ; le garde sinon. Renvoie le chemin du fichier."""
    path = Path(path) if path else token_path()
    if read_token(path):
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        os.chmod(path.parent, 0o700)
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    with os.fdopen(descriptor, "w", encoding="ascii") as stream:
        stream.write(secrets.token_urlsafe(32) + "\n")
    if os.name != "nt":
        os.chmod(path, 0o600)
    return path


def check_token(header, path=None):
    """Vrai si l'en-tête Authorization porte le jeton de la passerelle (comparaison à temps constant)."""
    expected = read_token(path)
    if not expected:
        raise GatewayError(404, "Passerelle vocale fermée : lance « python3 gateway.py --creer-jeton ».", "not_found")
    scheme, _, value = str(header or "").partition(" ")
    if scheme.lower() != "bearer" or not hmac.compare_digest(value.strip().encode(), expected.encode()):
        raise GatewayError(401, "Jeton de la passerelle refusé.", "authentication_error")


def heard_text(payload):
    """La phrase entendue : le dernier message de l'utilisateur, sans les consignes de l'application d'appel."""
    if not isinstance(payload, dict):
        raise GatewayError(400, "Corps JSON attendu.")
    model = payload.get("model")
    if model not in (None, "", MODEL):
        raise GatewayError(404, f"Modèle inconnu : seul « {MODEL} » répond ici.", "not_found")
    messages = payload.get("messages")
    if not isinstance(messages, list):
        raise GatewayError(400, "Champ messages manquant.")
    for item in reversed(messages):
        if isinstance(item, dict) and item.get("role") == "user":
            content = item.get("content")
            if isinstance(content, list):
                content = "".join(part.get("text", "") for part in content
                                  if isinstance(part, dict) and isinstance(part.get("text"), str))
            text = _LEADING_RULES.sub("", str(content or "")).strip()
            text = re.sub(r"\s+", " ", text)
            if not text:
                break
            return text[:MAX_HEARD]
    raise GatewayError(400, "Aucune phrase à transmettre à Kenobi.")


def current_session(state):
    """La séance où l'appel répond : la plus récente du cours actif, une séance en cours de préférence."""
    course = tutor.course_of(state)
    if not course:
        return None, None
    sessions = [item for item in course.get("sessions") or [] if isinstance(item, dict) and item.get("id")]
    if not sessions:
        return course, None
    active = [item for item in sessions if item.get("status") == "active"] or sessions
    return course, max(active, key=lambda item: str(item.get("updatedAt") or item.get("startedAt") or ""))


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _message_id():
    return f"message-{uuid.uuid4()}"


def _chunk(completion_id, created, delta=None, finish=None):
    return {"id": completion_id, "object": "chat.completion.chunk", "created": created, "model": MODEL,
            "choices": [{"index": 0, "delta": delta or {}, "finish_reason": finish}]}


def answer(server, payload, cancel=None):
    """Évènements d'un tour : ('text', morceau à dire)…, puis ('done', {reply}) ; les erreurs lèvent GatewayError.

    La phrase entendue est enregistrée dans la séance avant la réponse, la réponse complète après ; un tour coupé
    avant toute réponse est retiré (l'application d'appel reprend ces mots au tour suivant).
    """
    heard = heard_text(payload)
    message = f"[Appel vocal] {heard}"
    user_id = _message_id()
    holder = {}

    def add_question(state):
        course, session = current_session(state)
        if not session:
            return False
        now = _now()
        session.setdefault("messages", []).append({"id": user_id, "role": "user", "createdAt": now, "content": message,
                                                   "text": heard, "voice": True, "via": "gateway"})
        session["updatedAt"] = now
        holder["session"] = session["id"]
        return True

    server.update_state(add_question)
    if "session" not in holder:
        yield "text", "Ouvre d'abord une leçon dans Kenobi, puis rappelle-moi."
        yield "done", {"reply": "", "recorded": False}
        return
    state = server.load_state()
    segmenter = speech_text.StreamSegmenter()
    parts, result = [], {}
    events = tutor.stream_reply(state, {"sessionId": holder["session"], "message": message, "terminal": "closed"},
                                settings=server.settings, data_dir=server.data_dir, runtime_dir=server.runtime_dir,
                                phone=True, cancel=cancel)
    finished = False
    try:
        for kind, data in events:
            if kind == "delta":
                parts.append(data["text"])
                for piece in segmenter.feed(data["text"]):
                    yield "text", piece
            elif kind == "done":
                result = data
        for piece in segmenter.finish():
            yield "text", piece
        finished = True
    except tutor.ChatError as error:
        raise GatewayError(error.status if error.status >= 400 else 502, error.message, "kenobi_error") from None
    finally:
        events.close()
        _record_reply(server, holder["session"], user_id, "".join(parts).strip(), result, finished)
    yield "done", {"reply": "".join(parts).strip(), "recorded": True}


def _record_reply(server, session_id, user_id, reply, result, finished):
    def change(state):
        _course, session = tutor.find_session(state, session_id)
        if not session:
            return False
        messages = session.get("messages") or []
        if not reply:
            # Rien n'a été répondu : la question ne reste pas seule dans la discussion.
            kept = [item for item in messages if item.get("id") != user_id]
            if len(kept) == len(messages):
                return False
            session["messages"] = kept
            return True
        now = _now()
        entry = {"id": _message_id(), "role": "tutor", "createdAt": now, "content": reply, "voice": True,
                 "via": "gateway"}
        if not finished:
            entry["stopped"] = True
        for key in ("elapsed", "firstToken"):
            if isinstance(result.get(key), (int, float)):
                entry[key] = result[key]
        messages.append(entry)
        session["messages"] = messages
        session["updatedAt"] = now
        return True

    try:
        server.update_state(change)
    except Exception:
        pass  # la réponse a été dite ; ne pas transformer un souci d'enregistrement en erreur d'appel


def stream(server, payload, write, cancel=None):
    """Répond en SSE compatible OpenAI ; `write(bytes)` envoie au client. Une coupure du client arrête Kenobi."""
    completion_id = f"chatcmpl-kenobi-{uuid.uuid4().hex[:24]}"
    created = int(time.time())

    def send(value):
        write(("data: " + (value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)) + "\n\n")
              .encode("utf-8"))

    events = answer(server, payload, cancel=cancel)
    try:
        send(_chunk(completion_id, created, {"role": "assistant", "content": ""}))
        first = True
        for kind, data in events:
            if kind == "text":
                send(_chunk(completion_id, created, {"content": ("" if first else " ") + data}))
                first = False
        send(_chunk(completion_id, created, {}, "stop"))
        send("[DONE]")
    except GatewayError as error:
        send({"error": {"message": error.message, "type": error.kind}})
    except (BrokenPipeError, ConnectionResetError):
        raise
    except Exception:
        send({"error": {"message": "Erreur interne de Kenobi.", "type": "kenobi_error"}})
    finally:
        if cancel is not None:
            cancel.set()
        events.close()


def complete(server, payload):
    """Réponse entière (requête sans flux)."""
    pieces = []
    for kind, data in answer(server, payload):
        if kind == "text":
            pieces.append(data)
    return {"id": f"chatcmpl-kenobi-{uuid.uuid4().hex[:24]}", "object": "chat.completion", "created": int(time.time()),
            "model": MODEL, "choices": [{"index": 0, "message": {"role": "assistant", "content": " ".join(pieces)},
                                         "finish_reason": "stop"}]}


def new_cancel():
    return providers.Cancel()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Passerelle vocale de Kenobi (lignes d'appel extérieures).")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--creer-jeton", action="store_true", help="crée le jeton (s'il n'existe pas) et dit où il est")
    group.add_argument("--etat", action="store_true", help="dit si la passerelle est ouverte")
    args = parser.parse_args(argv)
    if args.creer_jeton:
        path = create_token()
        print(f"Jeton de la passerelle : {path} (0600). Donne ce chemin à l'application d'appel ; ne l'affiche pas.")
        return 0
    path = token_path()
    print(f"Passerelle {'ouverte' if read_token(path) else 'fermée'} ({path}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
