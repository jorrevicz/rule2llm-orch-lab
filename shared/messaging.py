"""Topologia de mensageria e configuração Celery comum aos dois serviços.

A topologia é declarada no RabbitMQ por `config/rabbitmq/definitions.json`; aqui as
filas são apenas referenciadas (`no_declare=True`), sem redeclaração pelos serviços.

Celery transporta e executa tarefas; não decide nada. Não há `autoretry`: o `RETRY`
experimental é uma nova tentativa lógica decidida pelo orquestrador (CLAUDE §9).
"""

from enum import StrEnum

from celery import Celery
from kombu import Exchange, Queue

TASKS_EXCHANGE = "tcc.tasks"
DEAD_LETTER_EXCHANGE = "tcc.dlx"
DEAD_LETTER_QUEUE = "tasks.dlq"


class Route(StrEnum):
    """Filas / routing keys do exchange `tcc.tasks` (docs/04 §4.1)."""

    INVENTORY_PRIMARY = "inventory.primary"
    INVENTORY_FALLBACK = "inventory.fallback"
    ORDERS_EVENTS = "orders.events"


# Nomes das tarefas Celery trocadas entre os serviços (publicadas via `send_task`,
# sem importar código do outro serviço).
INVENTORY_RESERVE_TASK = "inventory.reserve_stock"
ORDERS_HANDLE_EVENT_TASK = "orders.handle_inventory_event"

# Tarefas internas de coordenação do orders-service (fila orders.events, com atraso):
# verificação de timeout, despacho de nova tentativa e reavaliação após WAIT.
ORDERS_TIMEOUT_CHECK_TASK = "orders.timeout_check"
ORDERS_DISPATCH_ATTEMPT_TASK = "orders.dispatch_attempt"
ORDERS_REEVALUATE_TASK = "orders.reevaluate"


def declared_queue(route: Route) -> Queue:
    exchange = Exchange(TASKS_EXCHANGE, type="direct", no_declare=True)
    return Queue(str(route), exchange, routing_key=str(route), no_declare=True)


def dead_letter_queue() -> Queue:
    exchange = Exchange(DEAD_LETTER_EXCHANGE, type="direct", no_declare=True)
    return Queue(DEAD_LETTER_QUEUE, exchange, routing_key=DEAD_LETTER_QUEUE, no_declare=True)


def configure_celery(app: Celery, broker_url: str, *, prefetch_multiplier: int = 1) -> None:
    """Política comum de mensageria.

    `prefetch_multiplier = 1` (padrão): o worker só recebe a próxima mensagem após o ack
    da atual. Exceção: o worker do orders-service usa 0 (sem limite) porque consome as
    tarefas internas com atraso (timeout, nova tentativa, reavaliação) na mesma fila dos
    eventos; com limite 1, uma tarefa com atraso ocupa o slot de entrega e bloqueia os
    eventos até vencer (observado ao vivo: resposta retida por 2 s até o timeout).
    """
    app.conf.update(
        broker_url=broker_url,
        broker_connection_retry_on_startup=True,
        task_queues=[*(declared_queue(route) for route in Route), dead_letter_queue()],
        task_create_missing_queues=False,
        # Toda publicação deve informar a rota explicitamente. Uma tarefa enviada sem
        # rota cai na DLQ, visível para análise, em vez de ser processada em silêncio.
        task_default_queue=DEAD_LETTER_QUEUE,
        # Ack só após o processamento: uma queda do worker gera redelivery da MESMA
        # mensagem, que é tratada pela idempotência de transporte (message_id).
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        worker_prefetch_multiplier=prefetch_multiplier,
        task_serializer="json",
        accept_content=["json"],
        result_backend=None,
        task_ignore_result=True,
        # Sem filas auxiliares do Celery no broker (controle remoto e eventos).
        worker_enable_remote_control=False,
        worker_send_task_events=False,
        task_send_sent_event=False,
        enable_utc=True,
        timezone="UTC",
    )
