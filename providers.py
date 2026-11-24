"""Client des modèles d'IA : une conversation en flux, quel que soit le fournisseur.

Deux protocoles couvrent les services courants : « compatible OpenAI » (`/chat/completions`) et Anthropic
(`/messages`), tous deux en flux SSE. Bibliothèque standard seulement.

La clé part dans l'en-tête d'autorisation et nulle part ailleurs : ni journal, ni message d'erreur.
"""
import json
import re
import socket
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

CONNECT_TIMEOUT = 30
READ_TIMEOUT = 90
MAX_LINE = 1024 * 1024
MAX_REPLY = 200_000
ANTHROPIC_VERSION = "2023-06-01"
_NOT_CHAT = re.compile(r"embed|moderation|ocr|tts|transcri|whisper|dall-e|image|rerank|guard|realtime|voxtral", re.I)
_KEY_PROBLEM = re.compile(r"api[ _-]?key", re.IGNORECASE)
_OPENER = urllib.request.build_opener()
_LOCAL_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
# Ce qu'un couple (adresse, modèle) a déjà refusé : on ne le lui renvoie pas à chaque message.
_quirks = {}
_quirks_lock = threading.Lock()


class ProviderError(Exception):
    def __init__(self, status, message, code=""):
        super().__init__(message)
        self.status = status
        self.message = message
        self.code = code


class Cancel:
    """Arrêt demandé depuis un autre fil : ferme la réponse en cours de lecture."""

    def __init__(self):
        self._event = threading.Event()
        self._callbacks = []
        self._lock = threading.Lock()

    def is_set(self):
        return self._event.is_set()

    def on_set(self, callback):
        with self._lock:
            if not self._event.is_set():
                self._callbacks.append(callback)
                return
        callback()

    def set(self):
        with self._lock:
            if self._event.is_set():
                return
            self._event.set()
            callbacks, self._callbacks = self._callbacks, []
        for callback in callbacks:
            try:
                callback()
            except Exception:
                pass


def _opener(url):
    host = (urlsplit(url).hostname or "").lower()
    return _LOCAL_OPENER if host in ("127.0.0.1", "localhost", "::1") else _OPENER


def _abort(response):
    try:
        response.fp.raw._sock.shutdown(socket.SHUT_RDWR)
    except Exception:
        pass
    try:
        response.close()
    except Exception:
        pass


def _headers(config, stream=True):
    headers = {"Content-Type": "application/json", "User-Agent": "Kenobi/1.0"}
    if stream:
        headers["Accept"] = "text/event-stream"
    if config["kind"] == "anthropic":
        headers["anthropic-version"] = ANTHROPIC_VERSION
        if config.get("apiKey"):
            headers["x-api-key"] = config["apiKey"]
    elif config.get("apiKey"):
        headers["Authorization"] = "Bearer " + config["apiKey"]
    return headers


def _error_detail(body):
    """Phrase du fournisseur, s'il en donne une : courte, sans rien de ce qu'on lui a envoyé."""
    try:
        data = json.loads(body.decode("utf-8", "replace"))
    except ValueError:
        return ""
    error = data.get("error") if isinstance(data, dict) else None
    detail = error.get("message") if isinstance(error, dict) else error if isinstance(error, str) else None
    if not detail and isinstance(data, dict):
        detail = data.get("message") or data.get("detail")
    if isinstance(detail, list):
        detail = "; ".join(str(item.get("msg") or item) if isinstance(item, dict) else str(item) for item in detail[:3])
    return re.sub(r"\s+", " ", str(detail or "")).strip()[:220]


def _http_error(config, error, body=b""):
    label = config.get("label") or "Le fournisseur"
    detail = _error_detail(body)
    code = error.code
    if code in (401, 403):
        return ProviderError(502, f"Clé refusée par {label} : vérifie-la dans les réglages.", "auth")
    if code == 404:
        return ProviderError(502, f"Modèle « {config.get('model')} » ou adresse introuvable chez {label}.", "not_found")
    if code == 429:
        return ProviderError(503, f"Limite ou crédit atteint chez {label} : réessaie dans un instant.", "rate")
    if code in (400, 422):
        # Certains fournisseurs répondent 400, et non 401, à une clé absente ou fausse (Google).
        if _KEY_PROBLEM.search(detail):
            return ProviderError(502, f"Clé refusée par {label} : vérifie-la dans les réglages.", "auth")
        return ProviderError(502, f"{label} a refusé la demande{' : ' + detail if detail else '.'}", "bad_request")
    return ProviderError(502, f"{label} a répondu par une erreur {code}{' : ' + detail if detail else '.'}", "upstream")


def _open(config, path, payload=None, *, stream=True, timeout=CONNECT_TIMEOUT):
    url = config["baseUrl"].rstrip("/") + path
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(url, body, method="POST" if body is not None else "GET",
                                     headers=_headers(config, stream and body is not None))
    try:
        return _opener(url).open(request, timeout=timeout)
    except urllib.error.HTTPError as error:
        try:
            detail = error.read(4096)
        except Exception:
            detail = b""
        error.close()
        raise _HttpFailure(error, detail) from None
    except (urllib.error.URLError, OSError, socket.timeout, ValueError) as error:
        label = config.get("label") or "Le fournisseur"
        reason = getattr(error, "reason", error)
        raise ProviderError(503, f"{label} est injoignable ({str(reason)[:120]}).", "unreachable") from None


class _HttpFailure(Exception):
    def __init__(self, error, body):
        super().__init__(str(error.code))
        self.error = error
        self.body = body


def normalize_messages(messages):
    """Suite stricte utilisateur / assistant, attendue par plusieurs fournisseurs : rôles fusionnés, début utilisateur."""
    result = []
    for item in messages:
        role = "assistant" if item.get("role") == "assistant" else "user"
        content = str(item.get("content") or "").strip()
        if not content:
            continue
        if result and result[-1]["role"] == role:
            result[-1]["content"] += "\n\n" + content
        else:
            result.append({"role": role, "content": content})
    while result and result[0]["role"] == "assistant":
        result.pop(0)
    return result


def _quirk_key(config):
    return (config["baseUrl"], config["model"])


def _get_quirks(config):
    with _quirks_lock:
        return dict(_quirks.get(_quirk_key(config), {}))


def _learn(config, **facts):
    with _quirks_lock:
        _quirks.setdefault(_quirk_key(config), {}).update(facts)


def _restore(config, facts):
    """Oublie ce qui a été appris pendant un appel qui a fini par échouer : le réglage retiré n'était pas la cause."""
    with _quirks_lock:
        if facts:
            _quirks[_quirk_key(config)] = dict(facts)
        else:
            _quirks.pop(_quirk_key(config), None)


def _openai_payload(config, system, messages, max_tokens, temperature, quirks):
    payload = {
        "model": config["model"],
        "stream": True,
        "messages": ([{"role": "system", "content": system}] if system else []) + messages,
    }
    if max_tokens and quirks.get("max_key", "max_tokens"):
        payload[quirks.get("max_key", "max_tokens")] = max_tokens
    if temperature is not None and not quirks.get("no_temperature"):
        payload["temperature"] = temperature
    reasoning = config.get("reasoning") or "auto"
    if reasoning != "auto" and not quirks.get("no_reasoning"):
        payload["reasoning_effort"] = reasoning
    return payload


def _adapt(config, failure, payload):
    """Un réglage facultatif refusé (400/422) : on apprend lequel et on renvoie sans lui. Rien n'a été produit."""
    if failure.error.code not in (400, 422):
        return False
    text = failure.body.decode("utf-8", "replace").lower()
    if _KEY_PROBLEM.search(text):
        return False  # une clé fausse dite en 400 : aucun réglage à retirer, et rien à apprendre de travers
    if config["kind"] == "anthropic":
        if "max_tokens" in text and payload.get("max_tokens", 0) > 4096:
            _learn(config, max_cap=8192 if payload["max_tokens"] > 8192 else 4096)
            return True
        if "temperature" in text and "temperature" in payload:
            _learn(config, no_temperature=True)
            return True
        return False
    if "max_completion_tokens" in text and "max_tokens" in payload:
        _learn(config, max_key="max_completion_tokens")
        return True
    if "reasoning" in text and "reasoning_effort" in payload:
        _learn(config, no_reasoning=True)
        return True
    if "temperature" in text and "temperature" in payload:
        _learn(config, no_temperature=True)
        return True
    if ("max_tokens" in text or "max_completion_tokens" in text) and (
            "max_tokens" in payload or "max_completion_tokens" in payload):
        _learn(config, max_key="")
        return True
    optional = [name for name in ("reasoning_effort", "temperature", "max_tokens", "max_completion_tokens")
                if name in payload]
    if optional:
        _learn(config, no_reasoning=True, no_temperature=True, max_key="")
        return True
    return False


def _sse(response, deadline, cancel):
    """Évènements SSE (nom, données) d'une réponse en flux."""
    event, data = None, []
    while True:
        if cancel is not None and cancel.is_set():
            raise ProviderError(499, "Réponse interrompue.", "cancelled")
        if time.monotonic() > deadline:
            raise ProviderError(504, "Le modèle n'a pas terminé à temps.", "timeout")
        try:
            line = response.readline(MAX_LINE + 1)
        except (socket.timeout, OSError, ValueError):
            if cancel is not None and cancel.is_set():
                raise ProviderError(499, "Réponse interrompue.", "cancelled") from None
            raise ProviderError(504, "Le modèle ne répond plus.", "timeout") from None
        if not line:
            if data:
                yield event, "\n".join(data)
            return
        text = line.decode("utf-8", "replace").rstrip("\r\n")
        if not text:
            if data:
                yield event, "\n".join(data)
            event, data = None, []
        elif text.startswith(":"):
            continue
        elif text.startswith("event:"):
            event = text[6:].strip()
        elif text.startswith("data:"):
            data.append(text[5:].lstrip())


def _text_of(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):  # morceaux typés : on ne garde que le texte, pas le raisonnement
        return "".join(part.get("text", "") for part in content
                       if isinstance(part, dict) and part.get("type") in ("text", "output_text")
                       and isinstance(part.get("text"), str))
    return ""


def _read_openai(response, deadline, cancel):
    finished = None
    for _event, raw in _sse(response, deadline, cancel):
        if raw == "[DONE]":
            break
        try:
            chunk = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(chunk, dict):
            continue
        if chunk.get("error"):
            error = chunk["error"]
            detail = error.get("message") if isinstance(error, dict) else str(error)
            raise ProviderError(502, f"Le modèle a signalé une erreur : {str(detail)[:200]}", "upstream")
        for choice in chunk.get("choices") or []:
            if not isinstance(choice, dict):
                continue
            delta = choice.get("delta") if isinstance(choice.get("delta"), dict) else {}
            text = _text_of(delta.get("content"))
            if text:
                yield "delta", text
            if choice.get("finish_reason"):
                finished = choice["finish_reason"]
    yield "finish", finished or "stop"


def _read_anthropic(response, deadline, cancel):
    finished = None
    for event, raw in _sse(response, deadline, cancel):
        try:
            chunk = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(chunk, dict):
            continue
        kind = chunk.get("type") or event
        if kind == "error":
            error = chunk.get("error") if isinstance(chunk.get("error"), dict) else {}
            raise ProviderError(502, f"Le modèle a signalé une erreur : {str(error.get('message', ''))[:200]}", "upstream")
        if kind == "content_block_delta":
            delta = chunk.get("delta") if isinstance(chunk.get("delta"), dict) else {}
            if delta.get("type") == "text_delta" and isinstance(delta.get("text"), str) and delta["text"]:
                yield "delta", delta["text"]
        elif kind == "message_delta":
            delta = chunk.get("delta") if isinstance(chunk.get("delta"), dict) else {}
            if delta.get("stop_reason"):
                finished = "length" if delta["stop_reason"] == "max_tokens" else "stop"
        elif kind == "message_stop":
            break
    yield "finish", finished or "stop"


def stream_chat(config, system, messages, *, max_tokens=4000, temperature=0.4, timeout=180, cancel=None):
    """Génère ('delta', texte) au fil de la réponse, puis ('done', {elapsed, firstToken, truncated, model})."""
    if not config.get("configured"):
        raise ProviderError(409, "Aucun modèle d'IA n'est branché : ouvre les réglages pour en choisir un.", "unconfigured")
    messages = normalize_messages(messages)
    if not messages:
        raise ProviderError(400, "Message vide.", "empty")
    start = time.monotonic()
    response = None
    known = _get_quirks(config)
    for _attempt in range(4):
        quirks = _get_quirks(config)
        if config["kind"] == "anthropic":
            payload = {
                "model": config["model"], "stream": True, "messages": messages,
                "max_tokens": min(max_tokens or 4096, quirks.get("max_cap", 64000)),
            }
            if system:
                payload["system"] = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
            if temperature is not None and not quirks.get("no_temperature"):
                payload["temperature"] = temperature
            path = "/messages"
        else:
            payload = _openai_payload(config, system, messages, max_tokens, temperature, quirks)
            path = "/chat/completions"
        try:
            response = _open(config, path, payload)
            break
        except _HttpFailure as failure:
            if _adapt(config, failure, payload):
                continue
            _restore(config, known)
            raise _http_error(config, failure.error, failure.body) from None
    if response is None:
        _restore(config, known)
        raise ProviderError(502, f"{config.get('label') or 'Le fournisseur'} refuse les réglages de la demande.", "bad_request")

    if cancel is not None:
        cancel.on_set(lambda: _abort(response))
    try:
        response.fp.raw._sock.settimeout(READ_TIMEOUT)
    except Exception:
        pass
    reader = _read_anthropic if config["kind"] == "anthropic" else _read_openai
    first, total, finished = None, 0, "stop"
    try:
        for kind, value in reader(response, start + timeout, cancel):
            if kind == "finish":
                finished = value
                continue
            total += len(value)
            if total > MAX_REPLY:
                raise ProviderError(502, "Réponse du modèle trop longue.", "too_long")
            if first is None:
                first = time.monotonic() - start
            yield "delta", value
    finally:
        _abort(response)
    if first is None:
        raise ProviderError(502, "Le modèle a renvoyé une réponse vide.", "empty_reply")
    yield "done", {
        "elapsed": round(time.monotonic() - start, 2),
        "firstToken": round(first, 2),
        "truncated": finished == "length",
        "model": config["model"],
    }


def complete(config, system, messages, **options):
    """Réponse entière, pour les travaux sans affichage progressif."""
    parts = []
    result = {}
    for kind, value in stream_chat(config, system, messages, **options):
        if kind == "delta":
            parts.append(value)
        else:
            result = value
    return "".join(parts), result


def list_models(config):
    """Modèles de conversation proposés par le fournisseur : [{id, label}]."""
    if not config.get("baseUrl"):
        raise ProviderError(400, "Adresse de l'API manquante.", "unconfigured")
    path = "/models?limit=200" if config["kind"] == "anthropic" else "/models"
    try:
        with _open(config, path, None, stream=False, timeout=20) as response:
            raw = response.read(4 * 1024 * 1024)
    except _HttpFailure as failure:
        raise _http_error({**config, "model": ""}, failure.error, failure.body) from None
    try:
        data = json.loads(raw.decode("utf-8", "replace"))
    except ValueError:
        raise ProviderError(502, "Liste des modèles illisible.", "upstream") from None
    items = data.get("data") if isinstance(data, dict) else data
    if not isinstance(items, list):
        items = data.get("models") if isinstance(data, dict) and isinstance(data.get("models"), list) else []
    models, seen = [], set()
    for item in items:
        if isinstance(item, str):
            item = {"id": item}
        if not isinstance(item, dict):
            continue
        model_id = str(item.get("id") or item.get("name") or "").strip()
        if model_id.startswith("models/"):
            model_id = model_id[len("models/"):]
        if not model_id or model_id in seen or len(model_id) > 120:
            continue
        capabilities = item.get("capabilities") if isinstance(item.get("capabilities"), dict) else None
        if capabilities is not None and capabilities.get("completion_chat") is False:
            continue
        if capabilities is None and _NOT_CHAT.search(model_id):
            continue
        seen.add(model_id)
        label = str(item.get("display_name") or item.get("name") or "").strip()
        models.append({"id": model_id, "label": label if label and label != model_id else ""})
    models.sort(key=lambda entry: entry["id"].lower())
    return models[:500]
