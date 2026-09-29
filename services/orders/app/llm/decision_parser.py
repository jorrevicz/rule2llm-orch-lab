"""Leitura estrita da saída do LLM como decisão (docs/06 §6.8; RF-027; CLAUDE §23).

Sem correção de nenhum tipo: nada de ajustar maiúsculas, remover espaços, converter a
string "null" em null, extrair JSON de dentro de texto ou completar campos. A saída só
vira `ProposedDecision` se for um objeto JSON cujas chaves pertencem ao contrato
(`action`, `target`, `reason_code`) e cujos valores são texto ou null. Qualquer outra
coisa é saída malformada: a decisão é inválida e o executor aplica `ABORT`.

Campo ausente vira `None` na proposta; o `DecisionValidator` decide se isso é válido
(ex.: `WAIT` sem target) ou não (ex.: `action` ausente → `UNKNOWN_ACTION`).
"""

import json
from dataclasses import dataclass
from enum import StrEnum

from shared.decision import ProposedDecision

CONTRACT_FIELDS = frozenset({"action", "target", "reason_code"})


class ParseError(StrEnum):
    INVALID_JSON = "INVALID_JSON"
    NOT_AN_OBJECT = "NOT_AN_OBJECT"
    UNEXPECTED_FIELDS = "UNEXPECTED_FIELDS"
    INVALID_FIELD_TYPE = "INVALID_FIELD_TYPE"


@dataclass(frozen=True)
class ParseResult:
    proposal: ProposedDecision | None
    error: ParseError | None = None


def parse_decision(text: str) -> ParseResult:
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return ParseResult(None, ParseError.INVALID_JSON)
    if not isinstance(value, dict):
        return ParseResult(None, ParseError.NOT_AN_OBJECT)
    if not set(value) <= CONTRACT_FIELDS:
        return ParseResult(None, ParseError.UNEXPECTED_FIELDS)
    if any(not (field is None or isinstance(field, str)) for field in value.values()):
        return ParseResult(None, ParseError.INVALID_FIELD_TYPE)
    return ParseResult(
        ProposedDecision(
            action=value.get("action"),
            target=value.get("target"),
            reason_code=value.get("reason_code"),
        )
    )
