"""Faux fournisseur de modèles pour les tests : il parle « compatible OpenAI » et Anthropic, rejoue des réponses
préparées et garde ce qu'il a reçu. Aucun appel réel, aucune clé réelle."""
import json
import threading
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class StubLLM:
    def __init__(self, cors=False, port=0):
        # cors=True: answers like a provider reachable from a web page (the web version calls it from the browser).
        self.cors = cors
        self.requests = []
        self.replies = deque()
        self.rejected_params = set()
        self.models = [{"id": "stub-fast"}, {"id": "stub-large", "name": "Stub Large"}, {"id": "stub-embed"}]
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def end_headers(self):
                if stub.cors:
                    self.send_header("Access-Control-Allow-Origin", self.headers.get("Origin") or "*")
                    self.send_header("Access-Control-Allow-Headers", "authorization, content-type, x-api-key, "
                                     "anthropic-version, anthropic-dangerous-direct-browser-access")
                    self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                super().end_headers()

            def do_OPTIONS(self):
                self.send_response(204 if stub.cors else 501)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def _json(self, status, value):
                body = json.dumps(value).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                stub.requests.append({"path": self.path, "headers": {name.lower(): value for name, value in self.headers.items()}, "body": None})
                if self.path.startswith("/v1/models"):
                    self._json(200, {"data": stub.models})
                else:
                    self._json(404, {"error": {"message": "not found"}})

            def do_POST(self):
                try:
                    self._post()
                except (BrokenPipeError, ConnectionResetError):
                    pass  # le client a coupé : c'est justement ce que certains tests vérifient

            def _post(self):
                length = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(length) or b"{}")
                stub.requests.append({"path": self.path, "headers": {name.lower(): value for name, value in self.headers.items()}, "body": body})
                for name in stub.rejected_params:
                    if name in body:
                        self._json(400, {"error": {"message": f"Unsupported parameter: '{name}'"}})
                        return
                reply = stub.replies.popleft() if stub.replies else {"chunks": ["ok"]}
                if reply.get("status", 200) != 200:
                    self._json(reply["status"], reply.get("body", {"error": {"message": "refused"}}))
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                anthropic = self.path.endswith("/messages")
                for chunk in reply["chunks"]:
                    if anthropic:
                        event = {"type": "content_block_delta", "delta": {"type": "text_delta", "text": chunk}}
                        self.wfile.write(f"event: content_block_delta\ndata: {json.dumps(event)}\n\n".encode("utf-8"))
                    else:
                        event = {"choices": [{"index": 0, "delta": {"content": chunk}, "finish_reason": None}]}
                        self.wfile.write(f"data: {json.dumps(event)}\n\n".encode("utf-8"))
                    self.wfile.flush()
                if anthropic:
                    stop = {"type": "message_delta", "delta": {"stop_reason": reply.get("stop", "end_turn")}}
                    self.wfile.write(f"event: message_delta\ndata: {json.dumps(stop)}\n\n".encode("utf-8"))
                    self.wfile.write(b'event: message_stop\ndata: {"type": "message_stop"}\n\n')
                else:
                    end = {"choices": [{"index": 0, "delta": {}, "finish_reason": reply.get("stop", "stop")}]}
                    self.wfile.write(f"data: {json.dumps(end)}\n\ndata: [DONE]\n\n".encode("utf-8"))
                self.wfile.flush()

        self.server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self.server.daemon_threads = True
        self.base_url = f"http://127.0.0.1:{self.server.server_address[1]}/v1"
        self.thread = threading.Thread(target=lambda: self.server.serve_forever(poll_interval=0.01), daemon=True)
        self.thread.start()

    def say(self, *chunks, **options):
        self.replies.append({"chunks": list(chunks), **options})

    def fail(self, status, message="refused"):
        self.replies.append({"status": status, "body": {"error": {"message": message}}})

    def chat_requests(self):
        return [item for item in self.requests if item["body"] is not None]

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def config(self, kind="openai", **extra):
        return {"provider": "custom", "label": "Stub", "kind": kind, "baseUrl": self.base_url, "model": "stub-fast",
                "apiKey": "test-key-not-real", "reasoning": "none", "configured": True, **extra}
