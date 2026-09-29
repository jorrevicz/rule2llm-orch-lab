"""Carga e validação de `config/experiment_config.yml`.

O arquivo é a fonte de verdade dos parâmetros experimentais (RNF-022). Os modelos
rejeitam chaves desconhecidas para que um erro de digitação não vire um parâmetro
silenciosamente ignorado.
"""

import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, NonNegativeInt, PositiveInt

CONFIG_PATH_ENV = "EXPERIMENT_CONFIG_PATH"
DEFAULT_CONFIG_PATH = Path("config/experiment_config.yml")


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ExperimentSection(_Section):
    phase: Literal["pilot", "experiment"]
    decision_engine: Literal["RULES", "LLM"]
    repetitions: PositiveInt | None
    execution_order: list[Literal["RULES", "LLM"]] | None


class MessagingSection(_Section):
    inventory_timeout_ms: PositiveInt
    max_attempts: PositiveInt
    retry_delay_ms: NonNegativeInt
    wait_delay_ms: NonNegativeInt
    max_waits: NonNegativeInt
    fallback_max_attempts: NonNegativeInt
    queue_high_watermark: PositiveInt
    task_deadline_ms: PositiveInt


class ContextSection(_Section):
    recent_events_limit: PositiveInt


class LLMSection(_Section):
    runtime: Literal["ollama"]
    runtime_version: str | None
    model: str
    model_digest: str | None
    quantization: str | None
    temperature: float
    top_p: float
    max_tokens: PositiveInt
    seed: int | None
    session_memory: bool
    request_timeout_seconds: PositiveInt
    stream: bool
    response_format: Literal["json"]  # D-09
    num_ctx: PositiveInt
    keep_alive: str
    prompt_template: str


class WorkloadSection(_Section):
    dataset: str
    requests: PositiveInt | None
    rate_per_second: float | None
    seed: int | None


class FaultSection(_Section):
    type: Literal[
        "none",
        "overload",
        "intermittent_error",
        "timeout",
        "inconsistent_data",
        "recovery",
    ]
    seed: int | None


class ExperimentConfig(_Section):
    experiment: ExperimentSection
    messaging: MessagingSection
    context: ContextSection
    llm: LLMSection
    workload: WorkloadSection
    fault: FaultSection


def resolve_config_path(path: str | Path | None = None) -> Path:
    """Caminho explícito > variável de ambiente > caminho padrão do repositório."""
    if path is not None:
        return Path(path)
    return Path(os.environ.get(CONFIG_PATH_ENV, DEFAULT_CONFIG_PATH))


def load_experiment_config(path: str | Path | None = None) -> ExperimentConfig:
    """Lê o YAML e o valida contra o contrato de configuração."""
    config_path = resolve_config_path(path)
    with config_path.open(encoding="utf-8") as handle:
        raw = yaml.safe_load(handle)
    return ExperimentConfig.model_validate(raw)
