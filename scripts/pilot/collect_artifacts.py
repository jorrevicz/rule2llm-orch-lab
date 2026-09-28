"""Consolida os artefatos de rastreabilidade de uma execução (docs/10 §10.2).

    .venv/bin/python -m scripts.pilot.collect_artifacts [--execution-id PILOT_0001]

Executar ao fim da execução, ANTES de derrubar/resetar o ambiente (o reset restaura
os bancos). Produz, em `data/<pilot|experiment>/<EXECUTION_ID>/`:

- `task_events.jsonl`: exportado da tabela `task_events` do `orders.db` (fonte única
  da trajetória, D-01), ordenado por tarefa e ordem de registro;
- `<artefato>.jsonl` para cada artefato gravado em arquivos por processo
  (`states.*.jsonl`, `decisions.*.jsonl`, `microservices_logs.*.jsonl`), unidos e
  ordenados por `timestamp`. Os arquivos por processo são mantidos como dado bruto.
"""

import argparse
import json
import os
import sys
from pathlib import Path

from scripts.pilot.environment import ORDERS_DB, REPO_ROOT, query_sqlite_records
from shared.artifacts import Artifact, execution_dir

DEFAULT_DATA_ROOT = REPO_ROOT / "data"
MERGED_ARTIFACTS = (Artifact.STATES, Artifact.DECISIONS, Artifact.MICROSERVICES_LOGS)


def task_event_record(row: dict) -> dict:
    return {
        "execution_id": row["execution_id"],
        "task_id": row["task_id"],
        "message_id": row["message_id"],
        "event_seq": row["event_seq"],
        "event_type": row["event_type"],
        "service": row["service"],
        "target": row["target"],
        "timestamp": row["recorded_at"],
        "published_at": row["published_at"],
        "redelivered": bool(row["redelivered"]),
        "attempt_number": row["attempt_number"],
        "payload": None if row["payload_json"] is None else json.loads(row["payload_json"]),
    }


def export_task_events(execution_id: str, directory: Path) -> int:
    rows = query_sqlite_records(
        ORDERS_DB,
        "SELECT * FROM task_events WHERE execution_id = ? ORDER BY task_id, event_id",
        execution_id,
    )
    _write_jsonl(directory / f"{Artifact.TASK_EVENTS}.jsonl", [task_event_record(row) for row in rows])
    return len(rows)


def merge_shards(directory: Path, artifact: Artifact) -> int:
    records = []
    for shard in sorted(directory.glob(f"{artifact}.*.jsonl")):
        with shard.open(encoding="utf-8") as handle:
            records.extend(json.loads(line) for line in handle if line.strip())
    records.sort(key=lambda record: record.get("timestamp", ""))
    _write_jsonl(directory / f"{artifact}.jsonl", records)
    return len(records)


def _write_jsonl(path: Path, records: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")


def collect(execution_id: str, data_root: Path = DEFAULT_DATA_ROOT) -> dict[str, int]:
    directory = execution_dir(data_root, execution_id)
    if not directory.is_dir():
        raise SystemExit(f"execution directory not found: {directory}")
    counts = {str(Artifact.TASK_EVENTS): export_task_events(execution_id, directory)}
    for artifact in MERGED_ARTIFACTS:
        counts[str(artifact)] = merge_shards(directory, artifact)
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--execution-id", default=os.environ.get("EXECUTION_ID"))
    parser.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    args = parser.parse_args()
    if not args.execution_id:
        parser.error("--execution-id (ou EXECUTION_ID) é obrigatório")

    print(json.dumps(collect(args.execution_id, args.data_root), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
