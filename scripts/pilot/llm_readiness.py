"""Readiness e warm-up do LLM antes da janela de medição (RF-038, RF-039; CLAUDE §24–25).

    .venv/bin/python -m scripts.pilot.llm_readiness

Verifica, no Ollama do host (D-08):
- runtime respondendo e versão;
- modelo presente, com digest, quantização e tamanho lidos do próprio runtime;
- modelo descarregado e 1 inferência de warm-up com o prompt fixo (fora das métricas
  das tarefas): cada execução parte do modelo frio, e `model_load_ms` mede o
  carregamento de fato; também `warmup_inference_ms`;
- após o warm-up, modelo carregado 100% na GPU e com o contexto configurado;
- 1 inferência de prova com o modelo quente em até `PROBE_LIMIT_MS` (o limite de p95 da
  bancada de viabilidade, M5-T08): detecta runtime ocupado ou host degradado (achado do
  M6-T10: Low Power Mode levou a inferência de ~3 s para até 10,6 s).

Reprovação → `readiness_status = FAIL`: a execução não pode ser válida (não entra na
amostra) e o motivo é registrado. Nada é inventado: o que o runtime não informa fica null.
"""

import json
import sys
from dataclasses import asdict, dataclass, field

from services.orders.app.llm.ollama_client import LLMRuntimeError, LLMTimeout, OllamaClient
from services.orders.app.llm.prompt_builder import PromptBuilder
from shared.config import ExperimentConfig, load_experiment_config
from shared.system_state import SystemState

DEFAULT_OLLAMA_URL = "http://localhost:11434"
PROBE_LIMIT_MS = 7000.0  # critério 2 de viabilidade do modelo (docs/13-roadmap.md, M5)

# Estado neutro usado só para aquecer o modelo (não é tarefa do experimento).
WARMUP_STATE = {
    "execution_id": "PILOT_WARMUP",
    "task_id": "TASK_WARMUP",
    "state_id": "STATE_WARMUP",
    "timestamp": "2026-01-01T00:00:00.000Z",
    "current_event_seq": 1,
    "task": {
        "phase": "PENDING",
        "current_service": None,
        "current_target": None,
        "attempt_number": 1,
        "max_attempts": 3,
        "wait_count": 0,
        "max_waits": 2,
        "elapsed_ms": 0,
    },
    "service": {"status": "available", "latency_ms": None, "last_result": None},
    "messaging": {"queue_size": 0, "redelivered": False},
    "alternatives": {"fallback_available": True, "fallback_used": False, "alternative_targets": ["inventory.fallback"]},
    "recent_events": [{"event_type": "TASK_CREATED", "attempt_number": 1, "event_seq": 1}],
}


@dataclass
class LLMReadiness:
    status: str = "FAIL"
    runtime_version: str | None = None
    model: str | None = None
    model_digest: str | None = None
    quantization: str | None = None
    parameter_size: str | None = None
    processor: str | None = None          # como o runtime reporta (ex.: "100% GPU")
    context_length: int | None = None
    model_load_ms: float | None = None
    warmup_inference_ms: float | None = None
    probe_inference_ms: float | None = None
    failures: list[str] = field(default_factory=list)


def check(config: ExperimentConfig, base_url: str = DEFAULT_OLLAMA_URL) -> LLMReadiness:
    readiness = LLMReadiness(model=config.llm.model)
    client = OllamaClient(base_url, config.llm)
    try:
        readiness.runtime_version = client.version()
        _read_model(client, config, readiness)
        if readiness.failures:
            return readiness
        prompt = _warm_up(client, config, readiness)
        _read_placement(client, config, readiness)
        _probe(client, prompt, readiness)
    except (LLMRuntimeError, LLMTimeout) as error:
        readiness.failures.append(f"runtime: {error.__class__.__name__}: {error}")
    readiness.status = "PASS" if not readiness.failures else "FAIL"
    return readiness


def _read_model(client: OllamaClient, config: ExperimentConfig, readiness: LLMReadiness) -> None:
    tag = next((m for m in client.tags() if m.get("name") == config.llm.model), None)
    if tag is None:
        readiness.failures.append(f"model {config.llm.model} not available in the runtime")
        return
    readiness.model_digest = tag.get("digest")
    details = client.show().get("details", {})
    readiness.quantization = details.get("quantization_level")
    readiness.parameter_size = details.get("parameter_size")


def _warm_up(client: OllamaClient, config: ExperimentConfig, readiness: LLMReadiness) -> str:
    prompt = PromptBuilder.from_file(config.llm.prompt_template).build(SystemState.model_validate(WARMUP_STATE))
    client.unload()
    generation = client.generate(prompt)
    readiness.model_load_ms = generation.load_duration_ms
    readiness.warmup_inference_ms = generation.wall_ms
    return prompt


def _probe(client: OllamaClient, prompt: str, readiness: LLMReadiness) -> None:
    readiness.probe_inference_ms = client.generate(prompt).wall_ms
    if readiness.probe_inference_ms > PROBE_LIMIT_MS:
        readiness.failures.append(
            f"probe inference {readiness.probe_inference_ms:.0f} ms > {PROBE_LIMIT_MS:.0f} ms (runtime busy or host degraded)"
        )


def _read_placement(client: OllamaClient, config: ExperimentConfig, readiness: LLMReadiness) -> None:
    loaded = next((m for m in client.running() if m.get("name") == config.llm.model), None)
    if loaded is None:
        readiness.failures.append("model not loaded after warm-up")
        return
    size, size_vram = loaded.get("size"), loaded.get("size_vram")
    if isinstance(size, int) and isinstance(size_vram, int) and size > 0:
        readiness.processor = f"{round(100 * size_vram / size)}% GPU"
        if size_vram < size:
            readiness.failures.append(f"model only partially on GPU ({readiness.processor})")
    readiness.context_length = loaded.get("context_length")
    if isinstance(readiness.context_length, int) and readiness.context_length < config.llm.num_ctx:
        readiness.failures.append(
            f"loaded context {readiness.context_length} < configured num_ctx {config.llm.num_ctx}"
        )


def main() -> int:
    readiness = check(load_experiment_config())
    print(json.dumps(asdict(readiness), indent=2))
    return 0 if readiness.status == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
