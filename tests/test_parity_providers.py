"""browser/providers.js talks to the providers like providers.py: same requests, same reading, same errors.

Python reads prepared answers through a fake opener; the JavaScript version reads the same bytes through a fake
fetch, cut in random chunks. No network.
"""
import base64
import contextlib
import io
import json
import random
import unittest
import urllib.error

import providers

try:
    from parity_helper import NODE, js
except ImportError:  # run as tests.test_parity_providers
    from tests.parity_helper import NODE, js

HARNESS = "test:providers_harness"
RNG = random.Random(1124)
BROWSER_ONLY = "anthropic-dangerous-direct-browser-access"


def body_bytes(body):
    if isinstance(body, bytes):
        return body
    if isinstance(body, str):
        return body.encode("utf-8")
    return json.dumps(body, ensure_ascii=False).encode("utf-8")


def reply(body=b"", status=200):
    data = body_bytes(body)
    cuts = sorted(RNG.sample(range(1, len(data)), min(len(data) - 1, RNG.randint(0, 14)))) if len(data) > 1 else []
    return {"status": status, "base64": base64.b64encode(data).decode("ascii"), "cuts": cuts}


def fail(status, message="refused"):
    return reply({"error": {"message": message}}, status)


def openai_sse(*chunks, stop="stop"):
    events = [{"choices": [{"index": 0, "delta": {"content": chunk}, "finish_reason": None}]} for chunk in chunks]
    events.append({"choices": [{"index": 0, "delta": {}, "finish_reason": stop}]})
    return "".join(f"data: {json.dumps(event, ensure_ascii=False)}\n\n" for event in events) + "data: [DONE]\n\n"


def anthropic_sse(*chunks, stop="end_turn"):
    text = "".join("event: content_block_delta\ndata: " + json.dumps(
        {"type": "content_block_delta", "delta": {"type": "text_delta", "text": chunk}}, ensure_ascii=False) + "\n\n"
        for chunk in chunks)
    text += "event: message_delta\ndata: " + json.dumps({"type": "message_delta", "delta": {"stop_reason": stop}}) + "\n\n"
    return text + 'event: message_stop\ndata: {"type": "message_stop"}\n\n'


def config(kind="openai", **extra):
    return {"provider": "custom", "label": "Stub", "kind": kind, "baseUrl": "https://stub.test/v1/", "model": "stub-fast",
            "apiKey": "test-key-not-real", "reasoning": "none", "configured": True, **extra}


def call(replies, cfg=None, system="Système.", messages=None, **options):
    return {"config": cfg or config(), "system": system, "messages": messages or [{"role": "user", "content": "Salut"}],
            "options": options, "replies": replies}


class FakeOpener:
    def __init__(self, replies, requests):
        self.replies, self.requests, self.index = replies, requests, 0

    def open(self, request, timeout=None):
        self.requests.append({"url": request.full_url, "method": request.get_method(),
                              "headers": {name.lower(): value for name, value in request.header_items()},
                              "body": json.loads(request.data) if request.data is not None else None})
        answer = self.replies[min(self.index, len(self.replies) - 1)]
        self.index += 1
        data = base64.b64decode(answer["base64"])
        if answer["status"] != 200:
            raise urllib.error.HTTPError(request.full_url, answer["status"], "error", None, io.BytesIO(data))
        return io.BytesIO(data)


@contextlib.contextmanager
def replaying(replies, requests):
    opener = FakeOpener(replies, requests)
    saved = providers._opener
    providers._opener = lambda url: opener
    try:
        yield
    finally:
        providers._opener = saved


def describe(error):
    if isinstance(error, providers.ProviderError):
        return {"name": "ProviderError", "status": error.status, "message": error.message, "code": error.code}
    return {"name": type(error).__name__}


def python_run(calls):
    providers._quirks.clear()
    results = []
    for item in calls:
        requests, events, error = [], [], None
        options = {}
        if "maxTokens" in item["options"]:
            options["max_tokens"] = item["options"]["maxTokens"]
        if "temperature" in item["options"]:
            options["temperature"] = item["options"]["temperature"]
        with replaying(item["replies"], requests):
            try:
                for kind, value in providers.stream_chat(item["config"], item["system"], item["messages"], **options):
                    events.append([kind, {"keys": list(value), "truncated": value["truncated"], "model": value["model"]}]
                                  if kind == "done" else [kind, value])
            except Exception as caught:  # noqa: BLE001 - the kind of failure is compared
                error = describe(caught)
        for request in requests:
            request["headers"].pop("user-agent", None)  # browsers refuse to set it
        quirks = providers._quirks.get((item["config"].get("baseUrl"), item["config"].get("model")))
        results.append({"events": events, "error": error, "requests": requests, "quirks": dict(quirks) if quirks else None})
    return results


def without_browser_header(results, anthropic):
    for result in results:
        for request in result["requests"]:
            value = request["headers"].pop(BROWSER_ONLY, None)
            assert value == ("true" if anthropic else None), value
    return results


def scenarios():
    claude = config("anthropic")
    data = lambda event: f"data: {json.dumps(event, ensure_ascii=False)}\n\n"  # noqa: E731
    choice = lambda **fields: data({"choices": [fields]})  # noqa: E731
    long_line = "data: " + json.dumps({"choices": [{"delta": {"content": "x" * (providers.MAX_LINE + 500)}}]}) + "\n\n"
    out = {
        "openai basic": [call([reply(openai_sse("Bon", "jour", " !"))])],
        "no system, auto reasoning, no max, no temperature": [
            call([reply(openai_sse("ok"))], config(reasoning="auto"), "", maxTokens=0, temperature=None)],
        "reasoning high, other settings": [call([reply(openai_sse("ok"))], config(reasoning="high"), maxTokens=123, temperature=1)],
        "anthropic basic": [call([reply(anthropic_sse("Oui", ".", stop="max_tokens"))], claude, maxTokens=70000)],
        "anthropic defaults": [call([reply(anthropic_sse("Oui"))], claude, "", maxTokens=0, temperature=None)],
        "messages normalised": [call([reply(openai_sse("ok"))], messages=[
            {"role": "assistant", "content": "Avant."}, {"role": "user", "content": 42}, {"role": "user", "content": ["a", 1]},
            {"role": "assistant", "content": {"x": 1.5}}, {"role": "system", "content": True}, {"role": "user", "content": 0},
            {"role": "assistant", "content": "  \n "}, {"content": "  sans rôle  "}])],
        "unconfigured": [call([reply(openai_sse("ok"))], config(configured=False))],
        "empty": [call([reply(openai_sse("ok"))], messages=[{"role": "assistant", "content": "seul"}])],
        "label missing": [call([fail(500, "boom")], config(label=""))],
        "statuses": [call([fail(status, f"boom {status}")]) for status in (401, 403, 404, 405, 418, 422, 429, 500, 502, 503)],
        "status without body": [call([reply(b"", 500)]), call([reply(b"<html>Bad gateway</html>", 502)])],
        "max_completion_tokens": [
            call([fail(400, "Unsupported parameter: 'max_tokens'. Use 'max_completion_tokens' instead."), reply(openai_sse("ok"))],
                 maxTokens=50),
            call([reply(openai_sse("encore"))], maxTokens=50)],
        "reasoning refused": [call([fail(400, "Unsupported parameter: 'reasoning_effort'"), reply(openai_sse("un"))]),
                              call([reply(openai_sse("deux"))])],
        "temperature refused": [call([fail(422, "temperature: only 1 is allowed"), reply(openai_sse("ok"))])],
        "generic refusal": [call([fail(400, "Invalid request."), reply(openai_sse("ok"))])],
        "nothing explains": [call([fail(400, "This model's maximum context length is 8192 tokens.")]),
                             call([reply(openai_sse("ok"))])],
        "four refusals": [call([fail(400, "use max_completion_tokens"), fail(400, "reasoning is not supported"),
                                fail(400, "temperature is not supported"), fail(400, "max_completion_tokens is not supported"),
                                reply(openai_sse("jamais"))])],
        "key told in a 400": [call([fail(400, "API_KEY invalid. Please pass a valid API key.")]),
                              call([fail(422, "reasoning needs an api-key")])],
        "learnt, then another failure": [call([fail(400, "reasoning is not supported"), fail(500, "down")])],
        "known, then a failure": [call([fail(400, "temperature unsupported"), reply(openai_sse("ok"))]),
                                  call([fail(400, "reasoning unsupported"), fail(429, "slow down")]),
                                  call([reply(openai_sse("ok"))])],
        "anthropic temperature": [call([fail(400, "temperature is deprecated for this model"), reply(anthropic_sse("ok"))], claude)],
        "anthropic small max": [call([fail(400, "max_tokens is too large")], claude)],
        "anthropic caps": [call([fail(400, "max_tokens: 20000 > 8192"), reply(anthropic_sse("ok"))], claude, maxTokens=20000),
                           call([reply(anthropic_sse("ok"))], claude, maxTokens=5000),
                           call([fail(400, "max_tokens: 9000 > 4096"), reply(anthropic_sse("ok"))], claude, maxTokens=9000)],
        "anthropic six thousand": [call([fail(400, "max_tokens too big"), reply(anthropic_sse("ok"))], claude, maxTokens=6000)],
        "sse syntax": [call([reply(
            ": comment\r\n" + "event: message\r\n" + 'data: {"choices":\r\ndata: [{"delta":{"content":"Un"}}]}\r\n\r\n'
            + "data:" + json.dumps({"choices": [{"delta": {"content": " deux"}}]}) + "\r\r\n\r\n"
            + "data: pas du JSON\n\n" + "   \n" + ' data: {"choices":[{"delta":{"content":"no"}}]}\n\n'
            + "event: vide\n\n" + "data:\ndata:\n\n" + "\r\n" + choice(delta={"content": "é😀 trois"})
            + 'data: [DONE]\n\ndata: {"choices":[{"delta":{"content":"après"}}]}\n\n')])],
        "byte order mark": [call([reply("\ufeff" + choice(delta={"content": "perdu"}) + choice(delta={"content": "gardé"}))])],
        "last event without blank line": [call([reply(choice(delta={"content": "a"}) + 'data: {"choices":[{"delta":{"content":"b"}}]}')])],
        "invalid utf-8": [call([reply(b'data: {"choices":[{"delta":{"content":"caf\xc3\xa9 \xff\xfe ok"}}]}\n\n')])],
        "nan elsewhere": [call([reply('data: {"choices":[{"delta":{"content":"N"}}],"x":NaN,"y":-Infinity}\n\n')])],
        "long line": [call([reply(long_line + choice(delta={"content": "après la longue ligne"}))])],
        "choices variants": [call([reply(
            data({"choices": None}) + data({"choices": {}}) + data({"choices": "abc"}) + data({"choices": {"a": 1}})
            + data({"choices": [1, "x", None]}) + choice(delta="pas un objet") + choice(delta={"content": 5})
            + choice(delta={"content": [{"type": "text", "text": "A"}, {"type": "reasoning", "text": "R"},
                                        {"type": "output_text", "text": "B"}, {"type": "text", "text": 1}, {"text": "?"}, "C"]})
            + data({"choices": [{"delta": {"content": "C"}, "finish_reason": "length"}, {"delta": {"content": "D"}, "finish_reason": 0}]})
            + data([1, 2]) + data("texte") + data(3) + data(None) + choice(delta={"reasoning_content": "pensée"}))])],
        "finish values": [call([reply(choice(delta={"content": "a"}, finish_reason=True))]),
                          call([reply(choice(delta={"content": "a"}, finish_reason=""))]),
                          call([reply(choice(delta={"content": "a"}, finish_reason="length") + choice(delta={"content": "b"}))])],
        "choices not iterable": [call([reply(data({"choices": 5}))])],
        "stream errors": [call([reply(choice(delta={"content": "a"}) + data({"error": error}) + choice(delta={"content": "b"}))])
                          for error in ({}, "", 0, [], "boom", {"message": None}, {"code": 1}, 5, [1, "a"], True,
                                        {"message": "x" * 300}, {"message": {"detail": "nested"}}, 1.5, 1e-7)],
        "empty replies": [call([reply("")]), call([reply("data: [DONE]\n\n")]),
                          call([reply(choice(delta={"reasoning_content": "rien"}) + "data: [DONE]\n\n")])],
        "too long": [call([reply(openai_sse(*(["é" * 50000] * 5)))])],
        "astral characters count once": [call([reply(openai_sse("😀" * 60000, "😀" * 60000))])],
        "anthropic events": [call([reply(
            'event: content_block_delta\ndata: {"delta": {"type": "text_delta", "text": "A"}}\n\n'
            + 'event: message_stop\ndata: {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "B"}}\n\n'
            + 'data: {"type": "content_block_delta", "delta": {"type": "thinking_delta", "thinking": "T"}}\n\n'
            + 'data: {"type": "content_block_delta", "delta": {"type": "text_delta", "text": 5}}\n\n'
            + 'data: {"type": "content_block_delta", "delta": {"type": "text_delta", "text": ""}}\n\n'
            + 'data: {"type": "content_block_delta", "delta": "x"}\n\n' + "data: [DONE]\n\n"
            + 'data: {"type": "message_delta", "delta": {"stop_reason": "max_tokens"}}\n\n'
            + 'data: {"type": "message_delta", "delta": {"stop_reason": null}}\n\n'
            + 'data: {"type": "message_stop"}\n\n' + 'data: {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "C"}}\n\n')],
            claude)],
        "anthropic stop reasons": [call([reply(anthropic_sse("a", stop=stop))], claude) for stop in ("end_turn", "", "stop_sequence")],
        "anthropic errors": [call([reply(anthropic_sse("a") .replace("event: message_delta", "event: error\ndata: "
                                                                     + json.dumps({"type": "error", "error": error}) + "\n\nevent: message_delta"))],
                                  claude) for error in ({"message": "Overloaded"}, {"message": None}, {}, "texte", {"message": "y" * 300})],
    }
    return out


def model_cases():
    items = [
        {"id": "stub-large", "name": "Stub Large"}, {"id": "stub-fast"}, {"id": "text-embedding-3"}, "Plain-Model",
        {"name": "models/gemini-pro", "display_name": "Gemini Pro"}, {"id": "voxtral-mini"}, {"id": "WHISPER-1"},
        {"id": "mistral-ocr", "capabilities": {"completion_chat": True}}, {"id": "codestral", "capabilities": {"completion_chat": False}},
        {"id": "omni-moderation", "capabilities": {}}, {"id": "dall-e-3", "capabilities": None}, {"id": "stub-fast"},
        {"id": "x" * 121}, {"id": "😀" * 61}, {"id": "  spaced  ", "display_name": "spaced"}, {"id": 7}, {"id": 0, "name": "zero"},
        {"id": "", "name": ""}, {"id": "models/"}, 42, None, ["a"], {"id": "𝒜-model"}, {"id": "\uffff-model"}, {"id": "Ärger"},
        {"id": "b-model", "display_name": "  Joli nom  "}, {"id": "a-model", "name": {"x": 1}}, {"id": "gpt-4o-transcribe"},
        {"id": "gpt-image-1"}, {"id": "llama-guard-4"}, {"id": "gpt-4o-realtime"}, {"id": "rerank-v3"}, {"id": "tts-1"},
        {"id": "ſkip-ocr"}, {"id": "ımage-gen"},
    ]
    cases = [
        (config(), reply({"data": items})),
        (config(), reply(items)),
        (config("anthropic"), reply({"data": [{"id": "claude-x", "display_name": "Claude X", "type": "model"}], "has_more": False})),
        (config(), reply({"data": {"not": "a list"}, "models": [{"name": "models/gemini-flash"}]})),
        (config(), reply({"data": None, "models": "not a list"})),
        (config(), reply({"object": "list"})),
        (config(), reply("texte")),
        (config(), reply('"une chaîne"')),
        (config(), reply("<html>")),
        (config(), reply(b"\xff\xfe")),
        (config(), reply([f"m-{index:03d}" for index in range(600)])),
        (config(), fail(401, "nope")),
        (config(), fail(404, "nope")),
        (config(), fail(400, "bad")),
        (config(), reply(b"", 500)),
        (config(baseUrl=""), reply({"data": []})),
    ]
    return [{"config": cfg, "reply": answer} for cfg, answer in cases]


def python_models(cases):
    results = []
    for case in cases:
        requests = []
        with replaying([case["reply"]], requests):
            try:
                result = {"models": providers.list_models(case["config"]), "requests": requests}
            except Exception as caught:  # noqa: BLE001
                result = {"error": describe(caught), "requests": requests}
        for request in requests:
            request["headers"].pop("user-agent", None)
        results.append(result)
    return results


ERROR_BODIES = [
    {"error": {"message": "Bad thing"}}, {"error": "string error"}, {"error": {"message": {"nested": [1, 2.5, True, None]}}},
    {"message": "top-level message"}, {"detail": "detail text"},
    {"detail": [{"loc": ["body"], "msg": "field required"}, {"type": "x", "ctx": {"a": "l'été"}}, "plain", 3, 4]},
    {"error": {"code": 400}}, {"error": {"message": ""}, "message": "fallback"}, {"error": {"message": 0}, "detail": 1.5},
    [1, 2], "plain text", "  {\"error\": {\"message\": \"  spaced \\n\\t text  \"}}  ", b"\xef\xbb\xbf{\"error\": \"bom\"}",
    b"{\"error\": \"caf\xc3\xa9 \xff\"}", {"error": {"message": "x" * 300}}, {"error": {"message": "Ünïcödé 😀 " * 30}},
    '{"error": {"message": NaN}}', '{"detail": Infinity}', '{"detail": -Infinity}', {"detail": [[1, 2], {"msg": ""}]},
    {"error": {"message": "tab\there\u00a0nbsp\u2028sep"}}, {"error": {"message": "a\x00b\x1bc\x7fd\u200be\U0001f600"}},
    # Not 1e16 or -0.0 among the first three: once parsed, JavaScript cannot tell these floats from integers.
    {"detail": ["l'apostrophe", "les \"guillemets\"", "l'un et \"l'autre\"", "anti\\slash"]}, {"detail": [1e-5, 0.1, 123.456, 1e16, -0.0]},
    {"detail": {"b": 1, "a": [True, False, None]}}, {"detail": []}, {"detail": {}}, {"error": None, "message": "m"}, "", b"\x80",
]


@unittest.skipUnless(NODE, "node absent")
class ProviderParityTests(unittest.TestCase):
    maxDiff = 6000

    def test_streamed_conversations_are_the_same(self):
        named = scenarios()
        expected = {name: python_run(calls) for name, calls in named.items()}
        results = js(HARNESS, "runMany", list(named.values()), timeout=120)
        for (name, calls), result in zip(named.items(), results):
            anthropic = all(item["config"].get("kind") == "anthropic" for item in calls)
            with self.subTest(name):
                self.assertEqual(without_browser_header(result, anthropic), expected[name])

    def test_model_lists_are_the_same(self):
        cases = model_cases()
        results = js(HARNESS, "models", cases)
        for case, result in zip(cases, results):
            without_browser_header([result], case["config"]["kind"] == "anthropic")
        self.assertEqual(results, python_models(cases))

    def test_error_details_are_the_same(self):
        bodies = [body_bytes(body) for body in ERROR_BODIES]
        encoded = [base64.b64encode(body).decode("ascii") for body in bodies]
        self.assertEqual(js(HARNESS, "details", encoded), [providers._error_detail(body) for body in bodies])

    def test_a_relative_address_is_refused_before_any_request(self):
        # Known divergence: Python raises a bare ValueError (a 500 for the page); the browser must never resolve
        # the address against its own origin, so it refuses it with a ProviderError instead.
        item = call([reply(openai_sse("ok"))], config(baseUrl="v1"))
        with self.assertRaises(ValueError), replaying(item["replies"], []):
            list(providers.stream_chat(item["config"], "", item["messages"]))
        result = js(HARNESS, "run", [item])[0]
        self.assertEqual(result["requests"], [])
        self.assertEqual(result["error"], {"name": "ProviderError", "status": 503, "code": "unreachable",
                                           "message": "Stub est injoignable (unknown url type: 'v1/chat/completions')."})

    def test_messages_are_normalised_the_same(self):
        lists = [[], [{"role": "assistant", "content": "a"}], [{"role": "user", "content": " a "}, {"role": "user", "content": "b"},
                                                                 {"role": "assistant", "content": "c"}, {"role": "tool", "content": "d"}],
                 [{"role": "user", "content": None}, {"role": "user", "content": False}, {"role": "user", "content": [1, "x"]}],
                 [{"role": "assistant", "content": "x"}, {"role": "assistant", "content": "y"}, {"role": "user", "content": "\u3000z\u2003"}]]
        self.assertEqual(js(HARNESS, "normalize", lists), [providers.normalize_messages(messages) for messages in lists])


if __name__ == "__main__":
    unittest.main()
