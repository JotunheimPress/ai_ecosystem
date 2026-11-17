"""Fortuna: typed decisions over HTTP, the TypeSafe /v1/systemone shape, from JevK5 on llama.cpp.

    POST /v1/systemone   {"state": "...", "questions": {"<id>": {"type": "noul|choice|score",
                          "instructions": "...", "criteria": ...}}}
    GET  /v1/models      the model this instance serves
    GET  /health         200 once llama-server has the model loaded, else 503

The engine is JevK5 (github.com/allebee/jevk5, Apache-2.0): a GGUF served by llama-server, read
through jevk5.gguf.JevK5GGUF, which asks for the answer letters' log-probabilities and
renormalises them under the model's calibration temperature. Nothing is generated. JevK5's own
jevk5-serve imports torch, so this is the same request/response shape on the llama.cpp path:
`normalized` and the response body follow jevk5/server.py at the pinned commit.

Standard library only. llama-server listens on 127.0.0.1 inside the container and is never
exposed; this process is the only thing that talks to it.

Settings (environment, set by start.sh):
    FORTUNA_MODEL_NAME            reported in responses and /v1/models
    FORTUNA_TEMPERATURE           the GGUF file's letter temperature (from its model card)
    FORTUNA_KNOCKOUT_TEMPERATURE  its knockout temperature (questions with more than 16 options)
    FORTUNA_LLAMA_URL             default http://127.0.0.1:8080
    FORTUNA_PORT                  default 8009
    FORTUNA_API_KEY               optional; when set, /v1/* needs "Authorization: Bearer <key>"
"""

from __future__ import annotations

import hmac
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.environ.get("FORTUNA_VENDOR", "/opt/fortuna/vendor"))
from jevk5.gguf import JevK5GGUF  # noqa: E402  (vendored, standard library only)

LLAMA_URL = os.environ.get("FORTUNA_LLAMA_URL", "http://127.0.0.1:8080").rstrip("/")
PORT = int(os.environ.get("FORTUNA_PORT", "8009"))
MODEL_NAME = os.environ.get("FORTUNA_MODEL_NAME", "jevk5")
API_KEY = os.environ.get("FORTUNA_API_KEY") or None
STARTED = time.time()

# Bounds on one request, so a single caller can't queue minutes of GPU work. A question with more
# than 16 options costs ceil(n / 16) + 1 passes (jevk5.prompt.spread), each re-reading the state.
MAX_BODY = 1 << 20          # bytes
MAX_QUESTIONS = 32
MAX_OPTIONS = 128


def normalized(question: dict) -> dict:
    """Validate one question; a choice's criteria may be a list or {option: description}."""
    if not isinstance(question, dict):
        raise ValueError("each question must be an object")
    if question.get("type") not in ("noul", "choice", "score"):
        raise ValueError(f"unknown question type {question.get('type')!r}")
    if not isinstance(question.get("instructions"), str) or not question["instructions"].strip():
        raise ValueError("question is missing instructions")
    criteria = question.get("criteria")
    if question["type"] == "noul":
        if criteria is not None and not (
            isinstance(criteria, dict) and set(criteria) <= {"true", "false"}
        ):
            raise ValueError("noul criteria may only describe 'true' and 'false'")
    if question["type"] == "choice":
        if isinstance(criteria, list):
            criteria = dict.fromkeys(criteria)
        if not isinstance(criteria, dict) or len(criteria) < 2:
            raise ValueError("choice criteria must name at least two options")
        if not all(isinstance(k, str) and k for k in criteria):
            raise ValueError("choice options must be non-empty strings")
    if question["type"] == "score" and (not isinstance(criteria, list) or len(criteria) < 2):
        raise ValueError("score criteria must list at least two levels")
    if isinstance(criteria, (list, dict)) and len(criteria) > MAX_OPTIONS:
        raise ValueError(f"at most {MAX_OPTIONS} options per question")
    return {**question, "criteria": criteria}


def llama_ready() -> bool:
    try:
        with urllib.request.urlopen(LLAMA_URL + "/health", timeout=3) as r:
            return r.status == 200
    except (urllib.error.URLError, OSError):
        return False


def make_handler(model: JevK5GGUF):
    # llama-server runs one slot (start.sh: --parallel 1), so requests take turns here rather
    # than queueing inside it, where a slow one would time out the next.
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        server_version = "fortuna"

        def _send(self, status: int, payload: dict) -> None:
            data = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _authorized(self) -> bool:
            if API_KEY is None:
                return True
            got = self.headers.get("Authorization", "")
            return hmac.compare_digest(got.encode(), f"Bearer {API_KEY}".encode())

        def do_GET(self) -> None:
            path = self.path.split("?", 1)[0].rstrip("/")
            if path == "/health":
                ok = llama_ready()
                self._send(200 if ok else 503, {"ok": ok, "model": MODEL_NAME})
            elif path == "/v1/models":
                if not self._authorized():
                    self._send(401, {"error": "unauthorized"})
                    return
                self._send(200, {"models": [{
                    "name": MODEL_NAME,
                    "description": "JevK5 on llama.cpp, served by Fortuna (ai-stack)",
                    "engine": "jevk5-gguf",
                    "temperature": model.temperature,
                    "knockout_temperature": model.knockout_temperature,
                    "uptime_s": round(time.time() - STARTED),
                }]})
            else:
                self._send(404, {"error": "not found"})

        def do_POST(self) -> None:
            if self.path.split("?", 1)[0].rstrip("/") != "/v1/systemone":
                self._send(404, {"error": "not found"})
                return
            if not self._authorized():
                self._send(401, {"error": "unauthorized"})
                return
            try:
                length = int(self.headers.get("Content-Length", 0))
            except ValueError:
                length = -1
            if not 0 < length <= MAX_BODY:
                self._send(413 if length > MAX_BODY else 400,
                           {"error": f"body must be 1..{MAX_BODY} bytes of JSON"})
                return
            started = time.perf_counter()
            try:
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict):
                    raise ValueError("body must be a JSON object")
                state, questions = body.get("state"), body.get("questions")
                if not isinstance(state, (str, dict, list)) or not state:
                    raise ValueError("state must be a non-empty string or JSON value")
                if not isinstance(questions, dict) or not questions:
                    raise ValueError("questions must be a non-empty object")
                if len(questions) > MAX_QUESTIONS:
                    raise ValueError(f"at most {MAX_QUESTIONS} questions per request")
                checked = {str(qid): normalized(q) for qid, q in questions.items()}
            except (ValueError, TypeError, json.JSONDecodeError) as error:
                self._send(422, {"error": str(error)})
                return
            try:
                with lock:
                    answers = {qid: model.decide(state, q) for qid, q in checked.items()}
            except urllib.error.HTTPError as error:
                # llama-server refuses a prompt over its context with a 400: the caller's fault.
                detail = error.read().decode(errors="replace")[:300]
                status = 422 if error.code == 400 else 502
                self._send(status, {"error": f"model server: {detail or error.reason}"})
                return
            except (urllib.error.URLError, OSError, KeyError, IndexError) as error:
                self._send(502, {"error": f"model server unavailable: {error}"})
                return
            tokens = sum(a.pop("input_tokens") for a in answers.values())
            self._send(200, {
                "model": MODEL_NAME,
                "answers": answers,
                "usage": {"input_tokens": tokens, "output_tokens": 0},
                "latency_ms": round((time.perf_counter() - started) * 1e3, 2),
            })

        def log_message(self, fmt: str, *args) -> None:
            # One line per request, without bodies: states can hold story text.
            sys.stderr.write(f"{self.address_string()} {fmt % args}\n")

    return Handler


def main() -> int:
    model = JevK5GGUF(
        url=LLAMA_URL,
        temperature=float(os.environ["FORTUNA_TEMPERATURE"]),
        knockout_temperature=float(os.environ["FORTUNA_KNOCKOUT_TEMPERATURE"]),
        timeout_s=120.0,
    )
    server = ThreadingHTTPServer(("0.0.0.0", PORT), make_handler(model))
    server.daemon_threads = True
    print(f"fortuna: serving {MODEL_NAME} on :{PORT}/v1/systemone "
          f"(T={model.temperature}, knockout T={model.knockout_temperature}"
          f"{', API key required' if API_KEY else ''})", flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
