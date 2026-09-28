"""Exporta os contratos de mensagem (modelos em shared/envelope.py) para contracts/.

    python scripts/contracts/export_schemas.py

Os arquivos gerados são versionados; tests/contracts verifica que estão em dia
com o código. Rode este script sempre que o contrato mudar (mudança de contrato
tem impacto metodológico — CLAUDE §43).
"""

import json
from pathlib import Path

from shared.envelope import PAYLOAD_MODELS, MessageEnvelope

CONTRACTS_DIR = Path(__file__).resolve().parents[2] / "contracts"
ENVELOPE_SCHEMA_PATH = CONTRACTS_DIR / "message_envelope.schema.json"
PAYLOADS_SCHEMA_PATH = CONTRACTS_DIR / "message_payloads.schema.json"

JSON_SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"


def envelope_schema() -> dict:
    return {
        "$schema": JSON_SCHEMA_DIALECT,
        "$id": "message_envelope.schema.json",
        **MessageEnvelope.model_json_schema(),
    }


def payloads_schema() -> dict:
    return {
        "$schema": JSON_SCHEMA_DIALECT,
        "$id": "message_payloads.schema.json",
        "title": "MessagePayloads",
        "description": "Schema do campo `payload` por `event_type`.",
        "payloads_by_event_type": {
            str(event_type): model.model_json_schema()
            for event_type, model in sorted(PAYLOAD_MODELS.items())
        },
    }


def render(schema: dict) -> str:
    return json.dumps(schema, indent=2, ensure_ascii=False) + "\n"


def main() -> None:
    CONTRACTS_DIR.mkdir(exist_ok=True)
    ENVELOPE_SCHEMA_PATH.write_text(render(envelope_schema()), encoding="utf-8")
    PAYLOADS_SCHEMA_PATH.write_text(render(payloads_schema()), encoding="utf-8")
    print(f"wrote {ENVELOPE_SCHEMA_PATH.name}, {PAYLOADS_SCHEMA_PATH.name}")


if __name__ == "__main__":
    main()
