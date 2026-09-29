"""Custo de processamento simulado de uma solicitação de reserva (D-22).

Executado antes da transação do SQLite: o tempo de serviço (e, a partir do M6-T05, o
atraso injetado na rota primária) nunca segura o lock do `inventory.db`, que é
compartilhado pelos processos das duas rotas (D-21).
"""

import time
from collections.abc import Callable

from shared.envelope import MessageEnvelope


class ProcessingSimulator:
    def __init__(self, service_time_ms: int, *, sleep: Callable[[float], None] = time.sleep) -> None:
        self.service_time_ms = service_time_ms
        self._sleep = sleep

    def before_reservation(self, request: MessageEnvelope) -> None:
        """Tempo de serviço igual para as duas rotas e todos os cenários (D-22)."""
        if self.service_time_ms:
            self._sleep(self.service_time_ms / 1000)
