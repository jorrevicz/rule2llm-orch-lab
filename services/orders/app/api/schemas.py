"""Contratos HTTP do orders-service (RF-001, RF-002)."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StrictInt

from services.orders.app.db.models import OrderStatus


class OrderItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sku: Annotated[str, Field(min_length=1)]
    quantity: Annotated[StrictInt, Field(gt=0)]


class CreateOrderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: Annotated[list[OrderItem], Field(min_length=1)]


class OrderResponse(BaseModel):
    order_id: str
    task_id: str
    status: OrderStatus
