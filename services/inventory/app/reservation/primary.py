"""Rota primária de reserva (`inventory.primary`).

A reserva é simulada: não há estoque real a debitar (D-03 pendente). O ponto de
injeção controlada de falhas desta rota entra em M6-T03.
"""

from dataclasses import dataclass

from services.inventory.app.db.models import ReservationRoute, ReservationStatus


@dataclass(frozen=True)
class ReservationOutcome:
    route: ReservationRoute
    status: ReservationStatus


def reserve_primary(items: list[dict]) -> ReservationOutcome:
    return ReservationOutcome(route=ReservationRoute.PRIMARY, status=ReservationStatus.RESERVED)
