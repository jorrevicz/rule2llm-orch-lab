"""Carga e validação de `config/experiment_config.yml`.

O arquivo é a fonte de verdade dos parâmetros experimentais (RNF-022). Os modelos
rejeitam chaves desconhecidas para que um erro de digitação não vire um parâmetro
silenciosamente ignorado.
"""

import os
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    NonNegativeFloat,
    NonNegativeInt,
    PositiveFloat,
    PositiveInt,
    model_validator,
)

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


class InventorySection(_Section):
    service_time_ms: NonNegativeInt  # D-22: tempo de serviço simulado por solicitação
    catalog: str  # D-03: catálogo de SKUs carregado no inventory.db


class WorkloadSection(_Section):
    dataset: str
    requests: PositiveInt | None        # pedidos enviados, na ordem do dataset
    rate_per_second: PositiveFloat | None  # malha aberta: taxa fixa de envio
    seed: int | None                    # seed que gerou o dataset (seed_workload)


FaultType = Literal["none", "overload", "intermittent_error", "timeout", "inconsistent_data", "recovery"]

# Campos exigidos por tipo de falha (metodologia Código 11; D-20); os demais ficam null.
FAULT_FIELDS: dict[str, frozenset[str]] = {
    "none": frozenset(),
    "intermittent_error": frozenset({"target", "start_after_seconds", "duration_seconds", "failure_probability", "seed"}),
    "timeout": frozenset({"target", "start_after_seconds", "duration_seconds", "failure_probability", "delay_ms", "seed"}),
    "inconsistent_data": frozenset({"start_after_seconds", "duration_seconds", "failure_probability", "seed"}),
    "overload": frozenset({"start_after_seconds", "duration_seconds", "overload_rate_per_second"}),
    "recovery": frozenset({"target", "start_after_seconds", "duration_seconds"}),
}
FAULT_TARGETS: dict[str, str] = {
    "intermittent_error": "inventory.primary",   # degradação localizada: FALLBACK executável
    "timeout": "inventory.primary",
    "recovery": "inventory-service",             # indisponibilidade total: só WAIT/ABORT
}


class FaultSection(_Section):
    type: FaultType
    target: Literal["inventory.primary", "inventory-service"] | None = None
    start_after_seconds: NonNegativeFloat | None = None  # a partir do início da carga
    duration_seconds: PositiveFloat | None = None
    failure_probability: Annotated[float, Field(ge=0.0, le=1.0)] | None = None
    delay_ms: PositiveInt | None = None
    overload_rate_per_second: PositiveFloat | None = None
    seed: int | None = None

    @model_validator(mode="after")
    def _fields_match_the_type(self) -> "FaultSection":
        required = FAULT_FIELDS[self.type]
        optional = set(type(self).model_fields) - {"type"}
        missing = sorted(name for name in required if getattr(self, name) is None)
        unexpected = sorted(name for name in optional - required if getattr(self, name) is not None)
        if missing or unexpected:
            raise ValueError(f"fault {self.type}: missing {missing}, unexpected {unexpected}")
        if self.type in FAULT_TARGETS and self.target != FAULT_TARGETS[self.type]:
            raise ValueError(f"fault {self.type} must target {FAULT_TARGETS[self.type]}")
        return self


class ExperimentConfig(_Section):
    experiment: ExperimentSection
    messaging: MessagingSection
    context: ContextSection
    llm: LLMSection
    inventory: InventorySection
    workload: WorkloadSection
    fault: FaultSection


class ScenarioFile(_Section):
    """`config/scenarios/<cenário>.yml`: carga e falha de um cenário (RF-041).

    Substitui as seções `workload` e `fault` da config base; o mesmo arquivo vale para
    Rules e LLM no mesmo cenário (metodologia §4.4.1).
    """

    scenario_id: str
    description: str
    workload: WorkloadSection
    fault: FaultSection


def load_scenario(path: str | Path) -> ScenarioFile:
    with Path(path).open(encoding="utf-8") as handle:
        return ScenarioFile.model_validate(yaml.safe_load(handle))


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
