import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from scripts.pilot.llm_readiness import check
from tests.factories import repo_config

CONFIG = repo_config()
MODEL = CONFIG.llm.model
# O runtime falso se apresenta com os valores congelados na config (M8-T03).
VERSION, DIGEST, QUANTIZATION = CONFIG.llm.runtime_version, CONFIG.llm.model_digest, CONFIG.llm.quantization


def _routes(
    size_vram: int = 5_000,
    context_length: int = CONFIG.llm.num_ctx,
    models: list | None = None,
    version: str = VERSION,
    digest: str = DIGEST,
    quantization: str = QUANTIZATION,
) -> dict:
    return {
        "/api/version": {"version": version},
        "/api/tags": {"models": models if models is not None else [{"name": MODEL, "digest": digest}]},
        "/api/show": {"details": {"quantization_level": quantization, "parameter_size": "8.0B"}},
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
    assert (readiness.runtime_version, readiness.model_digest, readiness.quantization) == (VERSION, DIGEST, QUANTIZATION)
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
        ({"version": "0.34.0"}, "runtime_version '0.34.0' != frozen"),
        ({"digest": "0" * 64}, "model_digest"),
        ({"quantization": "Q8_0"}, "quantization 'Q8_0' != frozen 'Q4_K_M'"),
    ],
    indirect=["ollama"],
    ids=["model-missing", "partial-gpu", "short-context", "other-runtime", "other-digest", "other-quantization"],
)
def test_unfit_runtime_fails(ollama, failure):
    readiness = check(CONFIG, ollama.url)

    assert readiness.status == "FAIL"
    assert any(failure in message for message in readiness.failures)


def test_runtime_fields_left_null_in_the_config_are_not_checked(ollama):
    unfrozen = CONFIG.model_copy(update={"llm": CONFIG.llm.model_copy(update=dict.fromkeys(("runtime_version", "model_digest", "quantization")))})

    assert check(unfrozen, ollama.url).status == "PASS"


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


# M8-T23: o caminho das decisões (container → Ollama), não só o host


def test_runtime_reachable_from_every_decision_service_keeps_the_readiness():
    from scripts.pilot.llm_readiness import LLMReadiness, check_from_services

    asked = []
    readiness = check_from_services(LLMReadiness(status="PASS"), probe=lambda s: asked.append(s) or "0.33.2")

    assert readiness.status == "PASS"
    assert asked == ["orders-api", "orders-worker"]
    assert readiness.reachable_from_services == {"orders-api": "0.33.2", "orders-worker": "0.33.2"}


def test_runtime_unreachable_from_a_container_fails_even_if_the_host_passed():
    from scripts.pilot.llm_readiness import LLMReadiness, check_from_services
    from scripts.pilot.environment import InspectionError

    def probe(service: str) -> str:
        if service == "orders-worker":
            raise InspectionError("exec -T orders-worker: TimeoutError: timed out")
        return "0.33.2"

    readiness = check_from_services(LLMReadiness(status="PASS"), probe=probe)

    assert readiness.status == "FAIL"
    assert readiness.reachable_from_services == {"orders-api": "0.33.2", "orders-worker": None}
    assert readiness.failures == ["runtime unreachable from orders-worker: InspectionError: exec -T orders-worker: TimeoutError: timed out"]


def test_failures_already_found_on_the_host_are_kept():
    from scripts.pilot.llm_readiness import LLMReadiness, check_from_services

    readiness = check_from_services(LLMReadiness(status="FAIL", failures=["model x not available"]), probe=lambda s: "0.33.2")

    assert readiness.status == "FAIL" and readiness.failures == ["model x not available"]


def test_service_probe_uses_the_url_of_the_decisions():
    from scripts.pilot.llm_readiness import _SERVICE_PROBE

    assert "os.environ['OLLAMA_BASE_URL']" in _SERVICE_PROBE and "/api/version" in _SERVICE_PROBE
    compile(_SERVICE_PROBE, "<probe>", "exec")
