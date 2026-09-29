import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from scripts.pilot.llm_readiness import check
from tests.factories import repo_config

CONFIG = repo_config()
MODEL = CONFIG.llm.model


def _routes(size_vram: int = 5_000, context_length: int = CONFIG.llm.num_ctx, models: list | None = None) -> dict:
    return {
        "/api/version": {"version": "0.0.0-test"},
        "/api/tags": {"models": models if models is not None else [{"name": MODEL, "digest": "abc123"}]},
        "/api/show": {"details": {"quantization_level": "Q4_K_M", "parameter_size": "8.0B"}},
        "/api/ps": {"models": [{"name": MODEL, "size": 5_000, "size_vram": size_vram, "context_length": context_length}]},
        "/api/generate": {
            "response": '{"action": "CONTINUE", "target": null, "reason_code": "X"}',
            "done_reason": "stop",
            "load_duration": 1_500_000_000,
        },
    }


class FakeOllama:
    def __init__(self, routes: dict) -> None:
        self.calls: list[tuple[str, dict | None]] = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def _reply(self, body: dict | None):
                fake.calls.append((self.path, body))
                data = json.dumps(routes[self.path]).encode()
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):  # noqa: N802
                self._reply(None)

            def do_POST(self):  # noqa: N802
                self._reply(json.loads(self.rfile.read(int(self.headers["content-length"]))))

            def log_message(self, *args):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()


@pytest.fixture
def ollama(request):
    server = FakeOllama(_routes(**getattr(request, "param", {})))
    yield server
    server.close()


def test_ready_runtime_passes_and_reports_itself(ollama):
    readiness = check(CONFIG, ollama.url)

    assert readiness.status == "PASS", readiness.failures
    assert (readiness.runtime_version, readiness.model_digest, readiness.quantization) == ("0.0.0-test", "abc123", "Q4_K_M")
    assert readiness.processor == "100% GPU"
    assert readiness.model_load_ms == 1500.0
    assert readiness.warmup_inference_ms is not None


def test_model_is_unloaded_before_the_warm_up(ollama):
    check(CONFIG, ollama.url)

    generate_calls = [body for path, body in ollama.calls if path == "/api/generate"]
    assert generate_calls[0] == {"model": MODEL, "keep_alive": 0}   # descarrega
    assert "prompt" in generate_calls[1]                             # warm-up


@pytest.mark.parametrize(
    ("ollama", "failure"),
    [
        ({"models": []}, "not available"),
        ({"size_vram": 2_500}, "partially on GPU"),
        ({"context_length": 2048}, "num_ctx"),
    ],
    indirect=["ollama"],
    ids=["model-missing", "partial-gpu", "short-context"],
)
def test_unfit_runtime_fails(ollama, failure):
    readiness = check(CONFIG, ollama.url)

    assert readiness.status == "FAIL"
    assert any(failure in message for message in readiness.failures)


def test_unreachable_runtime_fails():
    readiness = check(CONFIG, "http://127.0.0.1:9")

    assert readiness.status == "FAIL"
    assert readiness.failures[0].startswith("runtime:")


def test_slow_probe_inference_fails_the_readiness(ollama, monkeypatch):
    from scripts.pilot import llm_readiness

    monkeypatch.setattr(llm_readiness, "PROBE_LIMIT_MS", -1.0)  # qualquer inferência é "lenta"

    readiness = check(CONFIG, ollama.url)

    assert readiness.status == "FAIL"
    assert readiness.probe_inference_ms is not None
    assert any("probe inference" in failure for failure in readiness.failures)
