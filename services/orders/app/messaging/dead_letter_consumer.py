"""Consumidor da `tasks.dlq` no worker do orders-service (D-07).

As mensagens mortas são tarefas Celery de outros consumidores (ex.:
`inventory.reserve_stock`), que este worker não sabe nem deve executar. Por isso a
fila é consumida em modo bruto (bootstep de consumidor do Celery), e cada mensagem
é convertida em `MESSAGE_DEAD_LETTERED` na trajetória da tarefa.
"""

import logging

from celery import bootsteps
from kombu import Consumer, Message

from services.orders.app.db.connection import connect
from services.orders.app.orchestration.dead_letters import DeadLetter, handle_dead_letter
from services.orders.app.settings import Settings
from shared.messaging import dead_letter_queue
from shared.structured_logging import correlated

logger = logging.getLogger(__name__)


def build_dead_letter_step(settings: Settings) -> type[bootsteps.ConsumerStep]:
    class DeadLetterConsumer(bootsteps.ConsumerStep):
        def get_consumers(self, channel):
            return [
                Consumer(
                    channel,
                    queues=[dead_letter_queue()],
                    callbacks=[self.on_message],
                    accept=["json"],
                )
            ]

        def on_message(self, body, message: Message) -> None:
            dead_letter = DeadLetter.from_celery_message(body, message.headers)
            try:
                connection = connect(settings.database_path)
                try:
                    changed = handle_dead_letter(connection, dead_letter)
                finally:
                    connection.close()
            except Exception:
                # Sem requeue (evita laço); a tasks.dlq não tem DLX: o conteúdo fica no log.
                logger.exception(
                    "dead letter could not be recorded: %s", body,
                    extra=correlated(task_id=dead_letter.task_id, message_id=dead_letter.message_id),
                )
                message.reject(requeue=False)
                return
            logger.warning(
                "message dead-lettered from %s (%s): %s",
                dead_letter.origin_queue,
                dead_letter.reason,
                dead_letter.task_name,
                extra=correlated(
                    task_id=dead_letter.task_id,
                    message_id=dead_letter.message_id,
                    event_type="MESSAGE_DEAD_LETTERED",
                    outcome="dead_lettered" if changed else "recorded",
                ),
            )
            message.ack()

    return DeadLetterConsumer
