"""Cliente HTTP do Ollama (docs/03 §3.5; D-08, D-09; RF-025, RF-026).

Cada chamada é independente (RNF-013): usa `/api/generate` sem o campo `context` (que
o Ollama usa para continuar uma conversa), com `stream: false` e `format: "json"`
(D-09). Os parâmetros de geração vêm do `experiment_config.yml`. O modelo não recebe
nenhuma ferramenta nem acesso ao ambiente: só texto de entrada e de saída (RNF-014).

Usa `urllib` da biblioteca padrão para não acrescentar dependência ao serviço.
"""

import json
import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from shared.config import LLMSection

NANOSECONDS_PER_MS = 1_000_000


class LLMTimeout(Exception):
    """A inferência excedeu `request_timeout_seconds` (→ `LLM_DECISION_TIMEOUT`)."""


class LLMRuntimeError(Exception):
    """Falha do runtime (conexão, HTTP, resposta ilegível): problema da bancada."""


@dataclass(frozen=True)
class Generation:
    text: str
    done_reason: str | None
    prompt_eval_count: int | None
    eval_count: int | None
    wall_ms: float                    # tempo da chamada medido pelo cliente
    total_duration_ms: float | None   # tempos informados pelo Ollama
    load_duration_ms: float | None


class OllamaClient:
    def __init__(self, base_url: str, llm: LLMSection, *, timeout_seconds: float | None = None) -> None:
        if llm.stream:
            raise ValueError("stream must be false: one complete response per decision")
        if llm.session_memory:
            raise ValueError("session_memory must be false: decisions are stateless (RNF-013)")
        self._base_url = base_url.rstrip("/")
        self._llm = llm
        self._timeout = timeout_seconds if timeout_seconds is not None else llm.request_timeout_seconds

    def request_body(self, prompt: str) -> dict[str, Any]:
        """Corpo enviado ao `/api/generate` (sem `context`: nada de memória conversacional)."""
        return {
            "model": self._llm.model,
            "prompt": prompt,
            "stream": False,
            "format": self._llm.response_format,
            "keep_alive": self._llm.keep_alive,
            "options": {
                "temperature": self._llm.temperature,
                "top_p": self._llm.top_p,
                "num_predict": self._llm.max_tokens,
                "seed": self._llm.seed,
                "num_ctx": self._llm.num_ctx,
            },
        }

    def generate(self, prompt: str) -> Generation:
        started = time.perf_counter()
        body = self._post("/api/generate", self.request_body(prompt))
        wall_ms = (time.perf_counter() - started) * 1000
        if not isinstance(body.get("response"), str):
            raise LLMRuntimeError("Ollama response without text")
        return Generation(
            text=body["response"],
            done_reason=body.get("done_reason"),
            prompt_eval_count=body.get("prompt_eval_count"),
            eval_count=body.get("eval_count"),
            wall_ms=wall_ms,
            total_duration_ms=_ms(body.get("total_duration")),
            load_duration_ms=_ms(body.get("load_duration")),
        )

    def version(self) -> str:
        return self._get("/api/version")["version"]

    def show(self) -> dict[str, Any]:
        return self._post("/api/show", {"model": self._llm.model})

    def tags(self) -> list[dict[str, Any]]:
        return self._get("/api/tags").get("models", [])

    def running(self) -> list[dict[str, Any]]:
        return self._get("/api/ps").get("models", [])

    def _get(self, path: str) -> dict[str, Any]:
        return self._request(urllib.request.Request(self._base_url + path))

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        request = urllib.request.Request(
            self._base_url + path,
            data=json.dumps(payload).encode(),
            headers={"content-type": "application/json"},
        )
        return self._request(request)

    def _request(self, request: urllib.request.Request) -> dict[str, Any]:
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                return json.load(response)
        except (TimeoutError, socket.timeout) as error:
            raise LLMTimeout(str(error)) from error
        except urllib.error.URLError as error:
            if isinstance(error.reason, (TimeoutError, socket.timeout)):
                raise LLMTimeout(str(error.reason)) from error
            raise LLMRuntimeError(str(error)) from error
        except (json.JSONDecodeError, OSError) as error:
            raise LLMRuntimeError(str(error)) from error


def _ms(nanoseconds: Any) -> float | None:
    return nanoseconds / NANOSECONDS_PER_MS if isinstance(nanoseconds, (int, float)) else None
