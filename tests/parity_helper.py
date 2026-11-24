"""Call the browser modules (browser/*.js) from the Python tests, to prove both versions behave the same.

    from parity_helper import NODE, js
    value = js("tutor", "buildSystem", state, "session-1", {"today": "2026-11-24"})

Each call starts Node with tests/parity/run.js. `js_many` sends several calls at once (one Node start).
"""
import json
import shutil
import subprocess
from pathlib import Path

RUNNER = Path(__file__).resolve().parent / "parity" / "run.js"
NODE = shutil.which("node")


class JsError(Exception):
    def __init__(self, message, name="Error"):
        super().__init__(message)
        self.message = message
        self.name = name


def _run(payload, timeout=60):
    if not NODE:
        raise RuntimeError("node is required for the parity tests")
    completed = subprocess.run([NODE, str(RUNNER)], input=json.dumps(payload, ensure_ascii=False),
                               capture_output=True, text=True, encoding="utf-8", timeout=timeout)
    if completed.returncode != 0:
        raise RuntimeError(f"node failed: {completed.stderr.strip()[:2000]}")
    return json.loads(completed.stdout)


def _value(answer):
    if not answer.get("ok"):
        raise JsError(answer.get("error", ""), answer.get("name", "Error"))
    return answer.get("value")


def js(module, call, *args, timeout=60):
    """Result of browser/<module>.js → <call>(*args); raises JsError if the function throws."""
    return _value(_run({"module": module, "call": call, "args": list(args)}, timeout))


def js_many(calls, timeout=120):
    """[(module, call, args), ...] → list of results or JsError instances (not raised)."""
    answers = _run([{"module": module, "call": call, "args": list(args)} for module, call, args in calls], timeout)
    results = []
    for answer in answers:
        try:
            results.append(_value(answer))
        except JsError as error:
            results.append(error)
    return results
