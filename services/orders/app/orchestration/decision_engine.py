"""Interface comum dos motores de decisão (docs/06 §6.1; RF-016, RF-017).

A única diferença experimental entre as condições é o motor que implementa esta
interface: `RulesDecisionEngine` ou `LLMDecisionEngine`. Ambos recebem o mesmo
`SYSTEM_STATE` e devolvem a mesma estrutura; validação e execução são comuns.
"""

from dataclasses import dataclass, field
from typing import Literal, Protocol

from shared.decision import ProposedDecision
from shared.system_state import SystemState

EngineName = Literal["RULES", "LLM"]


@dataclass(frozen=True)
class EngineOutput:
    """O que o motor devolveu num ponto de decisão.

    `proposal` é `None` quando a saída não pôde ser lida como decisão (ex.: JSON
    malformado de um LLM); `failure` descreve o motivo. `llm_inference_ms` e
    `token_usage` só se aplicam ao LLM.
    """

    proposal: ProposedDecision | None
    failure: str | None = None
    llm_inference_ms: float | None = None
    token_usage: dict[str, int | None] | None = field(default=None)


class DecisionEngine(Protocol):
    name: EngineName

    def decide(self, state: SystemState) -> EngineOutput: ...
