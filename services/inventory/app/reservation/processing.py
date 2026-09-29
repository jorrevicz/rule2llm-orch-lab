"""Processamento simulado de uma solicitação de reserva: custo e falha injetada.

Executado antes da transação do SQLite: o tempo de serviço (D-22) e o atraso injetado
nunca seguram o lock do `inventory.db`, que é compartilhado pelos processos das duas
rotas (D-21).

Falha injetada (D-20): se há `fault_control.json` ativo para a rota da solicitação e o
sorteio por (`seed`, `task_id`, `attempt_number`) cai abaixo de `failure_probability`:
- `timeout`: espera `delay_ms` a mais e processa normalmente (resposta tardia);
- `intermittent_error`: a rota falha; o serviço responde `transient_error`.
Cada aplicação vira `FAULT_APPLIED` em `fault_events` (informação do pesquisador,
nunca do decisor — metodologia Tabela 9).
"""

import time
from collections.abc import Callable

from shared.envelope import MessageEnvelope
from shared.faults import FaultControl, FaultEventRecorder, sampled
from shared.task import TaskResult


class ProcessingSimulator:
    def __init__(
        self,
        service_time_ms: int,
        *,
        fault_source: Callable[[], FaultControl | None] = lambda: None,
        fault_recorder: FaultEventRecorder | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.service_time_ms = service_time_ms
        self._fault_source = fault_source
        self._fault_recorder = fault_recorder
        self._sleep = sleep

    def before_reservation(self, request: MessageEnvelope) -> TaskResult | None:
        """Aplica o custo e a falha ativa; devolve a falha que a rota deve responder."""
        if self.service_time_ms:
            self._sleep(self.service_time_ms / 1000)  # igual para as duas rotas (D-22)
        fault = self._fault_source()
        if fault is None or fault.target != request.target:
            return None
        if not sampled(fault.seed, request.task_id, request.attempt_number, probability=fault.failure_probability):
            return None
        if fault.type == "timeout":
            self._record(fault, request, effect=f"delay_ms={fault.delay_ms}")
            self._sleep(fault.delay_ms / 1000)
            return None
        self._record(fault, request, effect=TaskResult.TRANSIENT_ERROR)
        return TaskResult.TRANSIENT_ERROR

    def _record(self, fault: FaultControl, request: MessageEnvelope, *, effect: str) -> None:
        if self._fault_recorder is None:
            return
        self._fault_recorder.record(
            "FAULT_APPLIED",
            fault_id=fault.fault_id,
            fault_type=fault.type,
            target=fault.target,
            task_id=request.task_id,
            message_id=request.message_id,
            event_seq=request.event_seq,
            attempt_number=request.attempt_number,
            effect=str(effect),
        )
