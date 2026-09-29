"""Prompt fixo do `LLMDecisionEngine` (metodologia Quadro 2; docs/06 §6.8; RNF-027).

O template é o texto do Quadro 2, versionado em `config/prompts/` e congelado antes da
coleta. A cada decisão, o prompt é reconstruído do zero: template fixo + o
`SYSTEM_STATE` corrente (o mesmo objeto entregue ao Rules), serializado em JSON. Não
há histórico além da janela `recent_events` que já faz parte do estado (RNF-012).
"""

import hashlib
import json
from pathlib import Path

from shared.system_state import SystemState

PLACEHOLDER = "{{SYSTEM_STATE}}"


class PromptBuilder:
    def __init__(self, template: str) -> None:
        if template.count(PLACEHOLDER) != 1:
            raise ValueError(f"template must contain {PLACEHOLDER} exactly once")
        self.template = template

    @classmethod
    def from_file(cls, path: str | Path) -> "PromptBuilder":
        return cls(Path(path).read_text(encoding="utf-8"))

    @property
    def template_hash(self) -> str:
        return hashlib.sha256(self.template.encode("utf-8")).hexdigest()

    def build(self, state: SystemState) -> str:
        return self.template.replace(PLACEHOLDER, serialize_state(state))


def serialize_state(state: SystemState) -> str:
    """JSON do estado, na ordem dos campos do contrato, indentado para legibilidade."""
    return json.dumps(state.model_dump(mode="json"), ensure_ascii=False, indent=2)
