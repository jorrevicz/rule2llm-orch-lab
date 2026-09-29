"""Rota alternativa de reserva (`inventory.fallback`), no próprio inventory-service.

Mesma semântica de domínio da rota primária (reservar os itens do pedido), por um
caminho operacional distinto: uma falha localizada na rota primária não a afeta
(docs/06 §6.5). Com o inventory-service inteiro fora do ar, também fica indisponível.
O ponto de injeção de falhas é só da rota primária (M6-T03).
"""

from services.inventory.app.db.models import ReservationRoute, ReservationStatus
from services.inventory.app.reservation.primary import ReservationOutcome


def reserve_fallback(items: list[dict]) -> ReservationOutcome:
    return ReservationOutcome(route=ReservationRoute.FALLBACK, status=ReservationStatus.RESERVED)
