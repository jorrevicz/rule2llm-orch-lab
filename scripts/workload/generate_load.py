"""Gerador de carga controlada (M6-T07; RF-040; metodologia §4.4.1, Tabela 14 etapa 7).

    .venv/bin/python -m scripts.workload.generate_load --config <config efetiva>  # mostra o plano

Normalmente é acionado pelo executor de cenário (`scripts/scenarios/run_scenario.py`),
que compartilha o instante zero com a linha do tempo da falha.

Malha aberta: cada pedido tem um instante de envio fixado de antemão; o envio não
espera a resposta do anterior. Assim a carga oferecida é a mesma para Rules e LLM,
e um decisor lento acumula trabalho em vez de reduzir a carga.

O plano é determinístico (mesma config → mesmo plano):
- pedidos = os `requests` primeiros do dataset, na ordem;
- intervalo = 1 / `rate_per_second`; na janela de `overload`, 1 / `overload_rate_per_second`;
- `inconsistent_data`: na janela, o pedido sorteado por hash de (`seed_fault`, índice)
  tem o SKU do primeiro item trocado por um SKU fora do catálogo (D-03). O mesmo pedido
  é alterado nas duas abordagens; cada troca vira `FAULT_APPLIED` em `fault_events`.

Cada envio vira uma linha de `workload.jsonl` (índice, instante previsto e real,
`order_id`, status HTTP, tempo de resposta).
"""

import argparse
import json
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path

from shared.artifacts import JsonlWriter
from shared.catalog import load_catalog, load_dataset
from shared.config import ExperimentConfig, FaultSection, load_experiment_config
from shared.faults import FaultEventRecorder, sampled
from shared.timestamps import utc_now_iso

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKLOAD_FILE = "workload.jsonl"
UNKNOWN_SKU = "SKU-999"         # fora do catálogo (D-03)
HTTP_TIMEOUT_SECONDS = 180.0    # o POST espera a decisão inicial (LLM pode acumular fila)
MAX_IN_FLIGHT = 256


@dataclass(frozen=True)
class PlannedOrder:
    index: int                     # posição no dataset
    offset_s: float                # instante de envio, a partir do início da carga
    items: tuple[dict, ...]
    replaced_sku: str | None = None  # inconsistent_data: SKU original trocado


def in_window(fault: FaultSection, offset_s: float) -> bool:
    if fault.start_after_seconds is None or fault.duration_seconds is None:
        return False
    return fault.start_after_seconds <= offset_s < fault.start_after_seconds + fault.duration_seconds


def send_offsets(config: ExperimentConfig) -> list[float]:
    workload, fault = config.workload, config.fault
    offsets, offset = [], 0.0
    for _ in range(workload.requests):
        offset = round(offset, 6)  # o mesmo valor decide a janela e é registrado
        offsets.append(offset)
        rate = workload.rate_per_second
        if fault.type == "overload" and in_window(fault, offset):
            rate = fault.overload_rate_per_second
        offset += 1.0 / rate
    return offsets


def build_plan(config: ExperimentConfig, repo_root: Path = REPO_ROOT) -> list[PlannedOrder]:
    workload, fault = config.workload, config.fault
    if workload.requests is None or workload.rate_per_second is None:
        raise ValueError("workload.requests and workload.rate_per_second are required")
    dataset = load_dataset(repo_root / workload.dataset)
    if workload.seed is not None and workload.seed != dataset.seed:
        raise ValueError(f"workload.seed {workload.seed} != dataset seed {dataset.seed}")
    if len(dataset.orders) < workload.requests:
        raise ValueError(f"dataset has {len(dataset.orders)} orders, {workload.requests} requested")
    if UNKNOWN_SKU in load_catalog(repo_root / config.inventory.catalog):
        raise ValueError(f"{UNKNOWN_SKU} must not be in the catalog")

    plan = []
    for order, offset in zip(dataset.orders, send_offsets(config)):
        items, replaced = order.items, None
        if (
            fault.type == "inconsistent_data"
            and in_window(fault, offset)
            and sampled(fault.seed, "order", order.index, probability=fault.failure_probability)
        ):
            replaced = items[0]["sku"]
            items = ({**items[0], "sku": UNKNOWN_SKU}, *items[1:])
        plan.append(PlannedOrder(order.index, offset, tuple(items), replaced))
    return plan


class LoadGenerator:
    """Envia o plano em malha aberta, numa thread própria, a partir de `start_monotonic`."""

    def __init__(
        self,
        plan: list[PlannedOrder],
        base_url: str,
        directory: Path,
        execution_id: str,
        *,
        fault: FaultSection,
    ) -> None:
        self._plan = plan
        self._base_url = base_url
        self._fault = fault
        self._records = JsonlWriter(directory / WORKLOAD_FILE)
        self._faults = FaultEventRecorder.for_process(directory, "workload", execution_id)
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self.sent = 0
        self.failed = 0

    def start(self, start_monotonic: float) -> None:
        self._thread = threading.Thread(target=self._run, args=(start_monotonic,), daemon=True)
        self._thread.start()

    def join(self) -> None:
        if self._thread is not None:
            self._thread.join()

    def _run(self, start_monotonic: float) -> None:
        with ThreadPoolExecutor(max_workers=MAX_IN_FLIGHT) as pool:
            for planned in self._plan:
                delay = start_monotonic + planned.offset_s - time.monotonic()
                if delay > 0:
                    time.sleep(delay)
                pool.submit(self._send, planned, start_monotonic)

    def _send(self, planned: PlannedOrder, start_monotonic: float) -> None:
        sent_at, started = utc_now_iso(), time.monotonic()
        body = json.dumps({"items": list(planned.items)}).encode()
        request = urllib.request.Request(
            f"{self._base_url}/orders", data=body, headers={"Content-Type": "application/json"}, method="POST"
        )
        status, response, error = None, {}, None
        try:
            with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as reply:
                status, response = reply.status, json.load(reply)
        except urllib.error.HTTPError as http_error:
            status, error = http_error.code, http_error.reason
        except OSError as os_error:  # conexão recusada, timeout do cliente
            error = repr(os_error)
        with self._lock:
            self.sent += 1
            self.failed += status != 202
        self._records.write(
            {
                "timestamp": sent_at,
                "order_index": planned.index,
                "scheduled_offset_s": planned.offset_s,
                "actual_offset_s": round(started - start_monotonic, 3),
                "order_id": response.get("order_id"),
                "task_id": response.get("task_id"),
                "http_status": status,
                "response_ms": round((time.monotonic() - started) * 1000, 1),
                "error": error,
                "inconsistent": planned.replaced_sku is not None,
            }
        )
        if planned.replaced_sku is not None:
            self._faults.record(
                "FAULT_APPLIED",
                fault_type="inconsistent_data",
                order_index=planned.index,
                order_id=response.get("order_id"),
                task_id=response.get("task_id"),
                effect=f"sku {planned.replaced_sku} -> {UNKNOWN_SKU}",
                seed=self._fault.seed,
            )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", type=Path, required=True, help="config efetiva da execução")
    args = parser.parse_args()
    plan = build_plan(load_experiment_config(args.config))
    for planned in plan:
        print(json.dumps(asdict(planned), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
