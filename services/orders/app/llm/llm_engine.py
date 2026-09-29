"""`LLMDecisionEngine`: seleção da ação por um LLM local (docs/06 §6.8; RF-025–RF-027).

    SYSTEM_STATE → prompt fixo → Ollama → leitura estrita → proposta

Recebe o mesmo `SYSTEM_STATE` do Rules e devolve a mesma estrutura (`EngineOutput`);
validação e execução são as mesmas (RNF-009, RNF-010). Stateless: cada decisão é uma
chamada independente com o prompt reconstruído (RNF-013). O modelo só produz texto;
não executa nada (RNF-014).

- Inferência acima de `request_timeout_seconds` → sem proposta, falha
  `LLM_DECISION_TIMEOUT` → `ABORT` (comportamento da abordagem, permanece na amostra).
- Falha do runtime (`LLMRuntimeError`) não é tratada aqui: é problema da bancada e
  sobe como exceção (a mensagem segue para a DLQ — D-07).
"""

import time

from services.orders.app.llm.decision_parser import parse_decision
from services.orders.app.llm.ollama_client import LLMTimeout, OllamaClient
from services.orders.app.llm.prompt_builder import PromptBuilder
from services.orders.app.orchestration.decision_engine import LLM_DECISION_TIMEOUT, EngineOutput
from shared.system_state import SystemState


class LLMDecisionEngine:
    name = "LLM"

    def __init__(self, client: OllamaClient, prompt_builder: PromptBuilder) -> None:
        self._client = client
        self._prompt_builder = prompt_builder

    def decide(self, state: SystemState) -> EngineOutput:
        prompt = self._prompt_builder.build(state)
        started = time.perf_counter()
        try:
            generation = self._client.generate(prompt)
        except LLMTimeout:
            return EngineOutput(
                proposal=None,
                failure=LLM_DECISION_TIMEOUT,
                llm_inference_ms=(time.perf_counter() - started) * 1000,
            )
        parsed = parse_decision(generation.text)
        return EngineOutput(
            proposal=parsed.proposal,
            failure=None if parsed.error is None else f"MALFORMED_OUTPUT:{parsed.error}",
            llm_inference_ms=generation.wall_ms,
            token_usage={
                "input_tokens": generation.prompt_eval_count,
                "output_tokens": generation.eval_count,
                "total_tokens": _total(generation.prompt_eval_count, generation.eval_count),
            },
            raw_response=generation.text,
            done_reason=generation.done_reason,
        )


def _total(input_tokens: int | None, output_tokens: int | None) -> int | None:
    if input_tokens is None or output_tokens is None:
        return None
    return input_tokens + output_tokens
