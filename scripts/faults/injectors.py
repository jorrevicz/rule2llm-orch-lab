"""Injetores de falha dos cenários (M6-T08; RF-041; metodologia §4.4, Tabela 14 etapa 8).

Cada injetor abre e fecha a janela da falha e registra `FAULT_STARTED` e `FAULT_ENDED`
em `fault_events.fault-injector.jsonl`, com os parâmetros da config e os instantes
previsto e real (a partir do início da carga):

- `PrimaryRouteFault` (`intermittent_error`, `timeout`): grava/apaga o
  `fault_control.json` da execução; o processo da rota primária aplica a falha às
  solicitações sorteadas (D-20). O fallback segue disponível.
- `InventoryOutage` (`recovery`): para e retoma o container do `inventory-service`
  (as duas rotas; só `WAIT`/`ABORT` fazem sentido). O fim registra quando os dois
  consumidores voltaram às filas.
- `WorkloadWindow` (`overload`, `inconsistent_data`): a perturbação é aplicada pelo
  gerador de carga; o injetor só registra a janela.
"""

import time
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from scripts.pilot.environment import compose, wait_until
from shared.config import FaultSection
from shared.faults import FaultControl, FaultEventRecorder, clear_control, write_control
from shared.timestamps import utc_now_iso

WRITER = "fault-injector"
INVENTORY_CONTAINER = "inventory-worker"
INVENTORY_QUEUES = ("inventory.primary", "inventory.fallback")
CONSUMERS_TIMEOUT_S = 60.0


class FaultInjector(Protocol):
    def start(self) -> dict: ...
    def stop(self) -> dict: ...


def _parameters(fault: FaultSection) -> dict:
    return fault.model_dump(mode="json")


def _window_offsets(fault: FaultSection) -> tuple[float, float]:
    """Início e fim previstos da janela; a validação da config garante os dois campos."""
    if fault.start_after_seconds is None or fault.duration_seconds is None:
        raise ValueError(f"fault {fault.type}: start_after_seconds and duration_seconds are required")
    return fault.start_after_seconds, fault.start_after_seconds + fault.duration_seconds


class _Window:
    """Registro comum da janela; subclasses aplicam e removem a perturbação."""

    def __init__(self, fault: FaultSection, recorder: FaultEventRecorder, clock: Callable[[], float]) -> None:
        self.fault = fault
        self.fault_id = f"FAULT_{fault.type.upper()}"
        self._recorder = recorder
        self._clock = clock  # segundos desde o início da carga

    def start(self) -> dict:
        self._apply()
        return self._record("FAULT_STARTED", _window_offsets(self.fault)[0])

    def stop(self) -> dict:
        details = self._remove()
        return self._record("FAULT_ENDED", _window_offsets(self.fault)[1], **details)

    def _record(self, event_type: str, scheduled: float, **details: object) -> dict:
        return self._recorder.record(
            event_type,
            fault_id=self.fault_id,
            fault_type=self.fault.type,
            parameters=_parameters(self.fault),
            scheduled_offset_s=scheduled,
            actual_offset_s=round(self._clock(), 3),
            **details,
        )

    def _apply(self) -> None:
        pass

    def _remove(self) -> dict:
        return {}


class PrimaryRouteFault(_Window):
    def __init__(self, fault: FaultSection, recorder: FaultEventRecorder, clock: Callable[[], float], directory: Path) -> None:
        super().__init__(fault, recorder, clock)
        self._directory = directory

    def _apply(self) -> None:
        fault = self.fault
        # Garantido pela validação da config (FAULT_FIELDS/FAULT_TARGETS); explicitado para o tipo.
        if (
            fault.type not in ("intermittent_error", "timeout")
            or fault.target != "inventory.primary"
            or fault.failure_probability is None
            or fault.seed is None
        ):
            raise ValueError(f"fault {fault.type}: not a primary-route fault")
        write_control(
            self._directory,
            FaultControl(
                fault_id=self.fault_id,
                type=fault.type,
                target=fault.target,
                failure_probability=fault.failure_probability,
                delay_ms=fault.delay_ms,
                seed=fault.seed,
                started_at=utc_now_iso(),
            ),
        )

    def _remove(self) -> dict:
        clear_control(self._directory)
        return {}


class InventoryOutage(_Window):
    def __init__(
        self,
        fault: FaultSection,
        recorder: FaultEventRecorder,
        clock: Callable[[], float],
        *,
        consumers: Callable[[], dict[str, int]],
    ) -> None:
        super().__init__(fault, recorder, clock)
        self._consumers = consumers

    def _apply(self) -> None:
        compose("stop", INVENTORY_CONTAINER)  # parada limpa; FAULT_STARTED após o efeito

    def _remove(self) -> dict:
        requested = round(self._clock(), 3)
        compose("start", INVENTORY_CONTAINER)
        back = wait_until(
            lambda: all(self._consumers().get(queue, 0) >= 1 for queue in INVENTORY_QUEUES), CONSUMERS_TIMEOUT_S
        )
        return {"restart_requested_offset_s": requested, "consumers_back": back}


class WorkloadWindow(_Window):
    """Sobrecarga e dados inconsistentes: aplicados pelo gerador de carga."""


def injector_for(
    fault: FaultSection,
    directory: Path,
    execution_id: str,
    clock: Callable[[], float],
    consumers: Callable[[], dict[str, int]],
) -> FaultInjector | None:
    if fault.type == "none":
        return None
    recorder = FaultEventRecorder.for_process(directory, WRITER, execution_id)
    if fault.type in {"intermittent_error", "timeout"}:
        return PrimaryRouteFault(fault, recorder, clock, directory)
    if fault.type == "recovery":
        return InventoryOutage(fault, recorder, clock, consumers=consumers)
    return WorkloadWindow(fault, recorder, clock)


def run_timeline(injector: FaultInjector, fault: FaultSection, start_monotonic: float) -> None:
    """Abre a janela em `start_after_seconds` e a fecha após `duration_seconds`.

    A falha é sempre removida, mesmo se algo falhar no meio (sem perturbação órfã).
    """
    start_offset, end_offset = _window_offsets(fault)
    _sleep_until(start_monotonic + start_offset)
    try:
        injector.start()
        _sleep_until(start_monotonic + end_offset)
    finally:
        injector.stop()


def _sleep_until(deadline: float) -> None:
    remaining = deadline - time.monotonic()
    if remaining > 0:
        time.sleep(remaining)
