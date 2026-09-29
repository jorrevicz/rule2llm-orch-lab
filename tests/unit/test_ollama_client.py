import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from services.orders.app.llm.ollama_client import LLMRuntimeError, LLMTimeout, OllamaClient
from tests.factories import repo_config

LLM = repo_config().llm
REPLY = {
    "model": LLM.model,
    "response": '{"action": "CONTINUE", "target": "inventory.primary", "reason_code": "NORMAL_FLOW"}',
    "done": True,
    "done_reason": "stop",
    "prompt_eval_count": 612,
    "eval_count": 24,
    "total_duration": 850_000_000,
    "load_duration": 12_000_000,
    "context": [1, 2, 3],
}


class FakeOllama:
    """Servidor HTTP local que imita o `/api/generate` do Ollama."""

    def __init__(self, reply=REPLY, delay: float = 0.0, status: int = 200) -> None:
        self.requests: list[dict] = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                length = int(self.headers["content-length"])
                fake.requests.append({"path": self.path, "body": json.loads(self.rfile.read(length))})
                time.sleep(delay)
                data = json.dumps(reply).encode() if isinstance(reply, dict) else reply
                self.send_response(status)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()


@pytest.fixture
def fake():
    server = FakeOllama()
    yield server
    server.close()


def test_request_uses_the_configured_generation_parameters(fake):
    OllamaClient(fake.url, LLM).generate("PROMPT")

    [request] = fake.requests
    assert request["path"] == "/api/generate"
    body = request["body"]
    assert body["model"] == LLM.model
    assert body["prompt"] == "PROMPT"
    assert body["stream"] is False
    assert body["format"] == "json"  # D-09
    assert body["options"] == {
        "temperature": LLM.temperature,
        "top_p": LLM.top_p,
        "num_predict": LLM.max_tokens,
        "seed": LLM.seed,
        "num_ctx": LLM.num_ctx,
    }


def test_each_call_is_independent(fake):
    client = OllamaClient(fake.url, LLM)
    client.generate("A")
    client.generate("B")

    # O Ollama devolve `context` para continuar a conversa; o cliente nunca o reenvia.
    assert all("context" not in request["body"] for request in fake.requests)
    assert [request["body"]["prompt"] for request in fake.requests] == ["A", "B"]


def test_generation_carries_text_tokens_and_timings(fake):
    generation = OllamaClient(fake.url, LLM).generate("PROMPT")

    assert generation.text == REPLY["response"]
    assert generation.done_reason == "stop"
    assert (generation.prompt_eval_count, generation.eval_count) == (612, 24)
    assert generation.total_duration_ms == 850.0
    assert generation.load_duration_ms == 12.0
    assert generation.wall_ms > 0


def test_slow_inference_is_a_timeout():
    slow = FakeOllama(delay=1.5)
    try:
        with pytest.raises(LLMTimeout):
            OllamaClient(slow.url, LLM, timeout_seconds=0.5).generate("PROMPT")
    finally:
        slow.close()


def test_unreachable_runtime_is_a_runtime_error():
    with pytest.raises(LLMRuntimeError):
        OllamaClient("http://127.0.0.1:9", LLM, timeout_seconds=1).generate("PROMPT")


@pytest.mark.parametrize(("reply", "status"), [(b"not json", 200), ({"error": "model not found"}, 404)])
def test_bad_runtime_responses_are_runtime_errors(reply, status):
    server = FakeOllama(reply=reply, status=status)
    try:
        with pytest.raises(LLMRuntimeError):
            OllamaClient(server.url, LLM).generate("PROMPT")
    finally:
        server.close()


@pytest.mark.parametrize("field", ["stream", "session_memory"])
def test_stateful_or_streaming_configuration_is_refused(field):
    with pytest.raises(ValueError):
        OllamaClient("http://127.0.0.1:9", LLM.model_copy(update={field: True}))
