# 09 — Dicionário de dados

> Escopo: **somente** os dois bancos SQLite — `orders.db` e `inventory.db`. Estrutura e
> relacionamentos em [08-modelo-de-dados-mer.md](08-modelo-de-dados-mer.md). Campos do
> envelope de mensagem, do `SYSTEM_STATE` e do contrato de decisão estão em
> [04](04-contrato-mensageria.md) e [06](06-modelo-de-decisao.md); artefatos de coleta
> (JSONL/CSV) em [10](10-rastreabilidade-e-metricas.md).
>
> **Afinidade SQLite:** `TEXT`, `INTEGER`, `REAL`. Booleanos como `INTEGER` (`0`/`1`).
> Datas como `TEXT` UTC ISO 8601 (`2026-08-27T12:00:00.000Z`).
> **Chave:** PK = primária, FK = estrangeira (dentro do mesmo banco), U = `UNIQUE`,
> IX = indexada.
> Itens marcados **⚠** vão além do piloto §23 (ver [08 §8.5](08-modelo-de-dados-mer.md)).

---

# Banco `orders.db` (orders-service)

## `executions` ⚠

Metadados de cada execução experimental (espelho de `execution_metadata.json`).

| Coluna | Afinidade | Nulo? | Chave | Default | Descrição | Exemplo |
|---|---|---|---|---|---|---|
| `execution_id` | TEXT | não | PK | — | Identificador da execução | `EXP_0042` / `PILOT_0001` |
| `phase` | TEXT | não | — | — | Fase da execução | `EXPERIMENT` \| `PILOT` |
| `scenario_id` | TEXT | não | IX | — | Cenário experimental | `NORMAL_FLOW`, `timeout`, `overload` |
| `decision_engine` | TEXT | não | — | — | Motor de decisão usado na execução | `rules` \| `llm` |
| `eligible_for_sample` | INTEGER | não | — | `0` | `1` só na coleta definitiva | `0` |
| `run_status` | TEXT | sim | — | `NULL` | Resultado da validação de integridade | `VALID` \| `INVALID` \| `NULL` |
| `invalid_reason` | TEXT | sim | — | `NULL` | Motivo quando `run_status = INVALID` | `readiness_failed` |
| `started_at` | TEXT | não | — | — | Início da execução | `2026-08-27T12:00:00.000Z` |
| `finished_at` | TEXT | sim | — | `NULL` | Fim da execução | `2026-08-27T12:14:31.520Z` |

## `orders`

Pedido e seu estado externo.

| Coluna | Afinidade | Nulo? | Chave | Default | Descrição | Exemplo |
|---|---|---|---|---|---|---|
| `order_id` | TEXT | não | PK | — | Identificador do pedido | `ORD_000187` |
| `execution_id` | TEXT | não | FK → `executions`, IX | — | Execução à qual o pedido pertence ⚠ (FK) | `EXP_0042` |
| `status` | TEXT | não | IX | `PENDING` | Estado externo do pedido | `PENDING` \| `COMPLETED` \| `FAILED` |
| `created_at` | TEXT | não | — | — | Criação | `2026-08-27T12:00:00.000Z` |
| `updated_at` | TEXT | não | — | — | Última atualização de estado | `2026-08-27T12:00:03.114Z` |

## `order_items` ⚠

Itens do pedido (normalização do `payload.items[]`).

| Coluna | Afinidade | Nulo? | Chave | Default | Descrição | Exemplo |
|---|---|---|---|---|---|---|
| `id` | INTEGER | não | PK (AUTOINCREMENT) | — | Chave técnica | `1` |
| `order_id` | TEXT | não | FK → `orders`, IX | — | Pedido | `ORD_000187` |
| `line_no` | INTEGER | não | U(`order_id`,`line_no`) | — | Número da linha no pedido | `1` |
| `sku` | TEXT | não | — | — | Código do item | `SKU-001` |
| `quantity` | INTEGER | não | — | — | Quantidade solicitada (> 0) | `2` |

## `tasks`

Tarefa de processamento; carrega todos os contadores usados no `SYSTEM_STATE`.

| Coluna | Afinidade | Nulo? | Chave | Default | Descrição | Exemplo |
|---|---|---|---|---|---|---|
| `task_id` | TEXT | não | PK | — | Identificador da tarefa | `TASK_000187` |
| `order_id` | TEXT | não | FK → `orders`, **U** | — | Pedido (1 tarefa por pedido no recorte atual) | `ORD_000187` |
| `execution_id` | TEXT | não | IX | — | Execução | `EXP_0042` |
| `status` | TEXT | não | IX | `PENDING` | Estado interno da tarefa (ver [05](05-maquina-de-estados.md)) | `RETRYING` |
| `phase` | TEXT | não | — | `PENDING` | Fase corrente exposta no `SYSTEM_STATE` | `RETRYING` |
| `current_service` | TEXT | sim | — | `NULL` | Serviço físico atual | `inventory-service` |
| `current_target` | TEXT | sim | — | `NULL` | Rota lógica atual | `inventory.primary` \| `inventory.fallback` \| `NULL` |
| `decision_engine` | TEXT | não | — | — | Motor de decisão da execução | `rules` \| `llm` |
| `attempt_number` | INTEGER | não | — | `1` | Tentativa lógica corrente (inicial = 1; ver [03 §3.7](03-stack-tecnologica.md)) | `2` |
| `max_attempts` | INTEGER | não | — | (config) | Limite total de tentativas | `3` |
| `wait_count` | INTEGER | não | — | `0` | Ações `WAIT` já realizadas | `0` |
| `max_waits` | INTEGER | não | — | (config) | Limite de esperas | `2` |
| `fallback_available` | INTEGER | não | — | `1` | Fallback admissível no fluxo | `1` |
| `fallback_used` | INTEGER | não | — | `0` | Fallback já consumido | `0` |
| `last_result` | TEXT | sim | — | `NULL` | Resultado da última etapa | `timeout`, `transient_error`, `invalid_data`, `fallback_failed`, `ok` |
| `current_event_seq` | INTEGER | não | — | `0` | Último `event_seq` da tarefa | `4` |
| `started_at` | TEXT | sim | — | `NULL` | Início do processamento (base de `elapsed_ms`) | `2026-08-27T12:00:00.100Z` |
| `deadline_at` | TEXT | sim | — | `NULL` | Prazo da tarefa (`started_at + task_deadline_ms`) | `2026-08-27T12:01:00.100Z` |
| `created_at` | TEXT | não | — | — | Criação | `2026-08-27T12:00:00.050Z` |
| `updated_at` | TEXT | não | — | — | Última atualização | `2026-08-27T12:00:04.342Z` |

## `task_events` ⚠

Espelho operacional de `task_events.jsonl` (o `StateBuilder` lê os últimos `K`).

| Coluna | Afinidade | Nulo? | Chave | Default | Descrição | Exemplo |
|---|---|---|---|---|---|---|
| `event_id` | INTEGER | não | PK (AUTOINCREMENT) | — | Chave técnica | `812` |
| `execution_id` | TEXT | não | IX | — | Execução | `EXP_0042` |
| `task_id` | TEXT | não | FK → `tasks`, IX | — | Tarefa | `TASK_000187` |
| `message_id` | TEXT | sim | IX | `NULL` | Mensagem associada (quando houver) | `MSG_0192` |
| `event_seq` | INTEGER | não | IX(`task_id`,`event_seq`) | — | Sequência lógica local (não é Lamport) | `4` |
| `event_type` | TEXT | não | — | — | Tipo do evento (ver [04 §4.6](04-contrato-mensageria.md)) | `INVENTORY_TIMEOUT` |
| `attempt_number` | INTEGER | não | — | — | Tentativa lógica no momento do evento | `2` |
| `service` | TEXT | não | — | — | Serviço que registrou | `orders` \| `inventory` |
| `target` | TEXT | sim | — | `NULL` | Rota, quando aplicável | `inventory.primary` |
| `redelivered` | INTEGER | não | — | `0` | `1` se foi reentrega da mesma mensagem | `0` |
| `published_at` | TEXT | sim | — | `NULL` | `published_at` do envelope | `2026-08-27T12:00:02.000Z` |
| `recorded_at` | TEXT | não | — | — | Instante de gravação do evento | `2026-08-27T12:00:02.014Z` |
| `payload_json` | TEXT | sim | — | `NULL` | Cópia do `payload` / detalhes | `{"order_id":"ORD_000187", ...}` |

## `states` ⚠

Espelho operacional de `states.jsonl`.

| Coluna | Afinidade | Nulo? | Chave | Default | Descrição | Exemplo |
|---|---|---|---|---|---|---|
| `state_id` | TEXT | não | PK | — | Identificador do snapshot apresentado ao decisor | `STATE_0091` |
| `execution_id` | TEXT | não | IX | — | Execução | `EXP_0042` |
| `task_id` | TEXT | não | FK → `tasks`, IX | — | Tarefa | `TASK_000187` |
| `current_event_seq` | INTEGER | não | — | — | `event_seq` no momento da construção | `4` |
| `phase` | TEXT | não | — | — | Fase no snapshot | `RETRYING` |
| `snapshot_json` | TEXT | não | — | — | `SYSTEM_STATE` **integral** (JSON) | `{"state_id":"STATE_0091", ...}` |
| `created_at` | TEXT | não | — | — | Instante de construção do snapshot | `2026-08-27T12:00:02.142Z` |

## `decisions` ⚠

Espelho operacional de `decisions.jsonl`.

| Coluna | Afinidade | Nulo? | Chave | Default | Descrição | Exemplo |
|---|---|---|---|---|---|---|
| `decision_id` | TEXT | não | PK | — | Identificador do ponto de decisão | `DEC_0091` |
| `execution_id` | TEXT | não | IX | — | Execução | `EXP_0042` |
| `task_id` | TEXT | não | FK → `tasks`, IX | — | Tarefa | `TASK_000187` |
| `state_id` | TEXT | não | FK → `states`, IX | — | Estado que originou a decisão | `STATE_0091` |
| `decision_engine` | TEXT | não | — | — | Motor que propôs | `rules` \| `llm` |
| `proposed_action` | TEXT | não | — | — | Ação proposta | `RETRY` |
| `proposed_target` | TEXT | sim | — | `NULL` | Target proposto | `inventory.primary` \| `NULL` |
| `proposed_reason_code` | TEXT | não | — | — | `reason_code` proposto | `TRANSIENT_RETRY` |
| `validation_valid` | INTEGER | não | — | — | Resultado da validação (`1`/`0`) | `1` |
| `validation_error` | TEXT | sim | — | `NULL` | Código de erro da validação | `UNKNOWN_TARGET` |
| `executed_action` | TEXT | não | — | — | Ação efetivamente executada | `RETRY` \| `ABORT` |
| `executed_target` | TEXT | sim | — | `NULL` | Target executado | `inventory.primary` \| `NULL` |
| `executed_reason_code` | TEXT | não | — | — | `reason_code` executado | `TRANSIENT_RETRY` \| `INVALID_DECISION` |
| `decision_time_ms` | REAL | não | — | — | Tempo entre `SYSTEM_STATE` pronto e ação válida disponível | `684.0` (LLM) / `1.7` (rules) |
| `llm_inference_ms` | REAL | sim | — | `NULL` | Parcela de inferência (só `llm`) | `642.0` \| `NULL` |
| `input_tokens` | INTEGER | sim | — | `NULL` | Tokens de entrada (quando confiável) | `428` |
| `output_tokens` | INTEGER | sim | — | `NULL` | Tokens de saída | `21` |
| `total_tokens` | INTEGER | sim | — | `NULL` | Soma | `449` |
| `created_at` | TEXT | não | — | — | Instante da decisão | `2026-08-27T12:00:02.196Z` |

## `processed_events`

Idempotência de transporte no consumo de `orders.events`.

| Coluna | Afinidade | Nulo? | Chave | Default | Descrição | Exemplo |
|---|---|---|---|---|---|---|
| `message_id` | TEXT | não | PK | — | Mensagem já processada | `MSG_0311` |
| `task_id` | TEXT | não | IX | — | Tarefa correlata | `TASK_000187` |
| `event_type` | TEXT | não | — | — | Tipo do evento processado | `STOCK_RESERVATION_SUCCEEDED` |
| `processed_at` | TEXT | não | — | — | Instante do processamento | `2026-08-27T12:00:03.100Z` |

## `outbox` ⚠

Publicação transacional para `tcc.tasks` (evita perder/duplicar mensagem entre commit e publish).

| Coluna | Afinidade | Nulo? | Chave | Default | Descrição | Exemplo |
|---|---|---|---|---|---|---|
| `id` | INTEGER | não | PK (AUTOINCREMENT) | — | Chave técnica | `77` |
| `task_id` | TEXT | não | FK → `tasks`, IX | — | Tarefa | `TASK_000187` |
| `message_id` | TEXT | não | **U** | — | `message_id` do envelope | `MSG_0192` |
| `target` | TEXT | não | — | — | Fila destino | `inventory.primary` |
| `event_type` | TEXT | não | — | — | Tipo do evento | `STOCK_RESERVATION_REQUESTED` |
| `envelope_json` | TEXT | não | — | — | Envelope completo serializado | `{"schema_version":"1.0", ...}` |
| `status` | TEXT | não | IX | `PENDING` | Estado de publicação | `PENDING` \| `SENT` |
| `created_at` | TEXT | não | — | — | Criação | `2026-08-27T12:00:00.900Z` |
| `sent_at` | TEXT | sim | — | `NULL` | Confirmação de publicação | `2026-08-27T12:00:00.905Z` |

---

# Banco `inventory.db` (inventory-service)

## `stock` ⚠

Suporte à reserva **simulada** e ao cenário "dados inconsistentes".

| Coluna | Afinidade | Nulo? | Chave | Default | Descrição | Exemplo |
|---|---|---|---|---|---|---|
| `sku` | TEXT | não | PK | — | Código do item | `SKU-001` |
| `quantity_available` | INTEGER | não | — | — | Quantidade disponível | `100` |
| `quantity_reserved` | INTEGER | não | — | `0` | Quantidade reservada acumulada | `12` |
| `updated_at` | TEXT | não | — | — | Última atualização | `2026-08-27T12:00:03.001Z` |

## `reservations`

Reserva efetivada. `task_id UNIQUE` é a **idempotência de negócio**.

| Coluna | Afinidade | Nulo? | Chave | Default | Descrição | Exemplo |
|---|---|---|---|---|---|---|
| `reservation_id` | TEXT | não | PK | — | Identificador da reserva | `RES_000187` |
| `task_id` | TEXT | não | **U**, IX | — | Tarefa — impede segunda reserva para o mesmo `task_id` | `TASK_000187` |
| `order_id` | TEXT | não | IX | — | Pedido correlato | `ORD_000187` |
| `execution_id` | TEXT | não | IX | — | Execução ⚠ | `EXP_0042` |
| `status` | TEXT | não | — | — | Resultado | `RESERVED` \| `FAILED` |
| `route` | TEXT | não | — | — | Rota usada | `primary` \| `fallback` |
| `created_at` | TEXT | não | — | — | Criação | `2026-08-27T12:00:02.900Z` |
| `updated_at` | TEXT | não | — | — | Última atualização | `2026-08-27T12:00:02.900Z` |

> No piloto §23.2 a tabela é `reservations(id AUTOINCREMENT, task_id UNIQUE, order_id,
> status, route, created_at)`. Aqui: `reservation_id` TEXT como PK, mais `execution_id` e
> `updated_at` ⚠.

## `reservation_items` ⚠

Itens reservados (normalização).

| Coluna | Afinidade | Nulo? | Chave | Default | Descrição | Exemplo |
|---|---|---|---|---|---|---|
| `id` | INTEGER | não | PK (AUTOINCREMENT) | — | Chave técnica | `1` |
| `reservation_id` | TEXT | não | FK → `reservations`, IX | — | Reserva | `RES_000187` |
| `line_no` | INTEGER | não | U(`reservation_id`,`line_no`) | — | Linha | `1` |
| `sku` | TEXT | não | — | — | Item | `SKU-001` |
| `quantity` | INTEGER | não | — | — | Quantidade reservada | `2` |

## `processed_messages`

Idempotência de transporte no consumo de `inventory.primary` / `inventory.fallback`.

| Coluna | Afinidade | Nulo? | Chave | Default | Descrição | Exemplo |
|---|---|---|---|---|---|---|
| `message_id` | TEXT | não | PK | — | Mensagem já processada | `MSG_0192` |
| `task_id` | TEXT | não | IX | — | Tarefa correlata | `TASK_000187` |
| `event_type` | TEXT | não | — | — | Tipo processado | `STOCK_RESERVATION_REQUESTED` |
| `result` | TEXT | não | — | — | Resultado registrado (reusado em redelivery) | `succeeded` \| `failed` |
| `processed_at` | TEXT | não | — | — | Instante do processamento | `2026-08-27T12:00:02.905Z` |

> No piloto §23.2: `processed_messages(message_id PK, task_id, processed_at)`. Coluna
> `event_type` e `result` são ⚠ (permitem reemitir o resultado correto em redelivery).

## `published_events` ⚠

Outbox para publicação confiável em `orders.events`.

| Coluna | Afinidade | Nulo? | Chave | Default | Descrição | Exemplo |
|---|---|---|---|---|---|---|
| `id` | INTEGER | não | PK (AUTOINCREMENT) | — | Chave técnica | `54` |
| `task_id` | TEXT | não | IX | — | Tarefa | `TASK_000187` |
| `message_id` | TEXT | não | **U** | — | `message_id` do evento publicado | `MSG_0311` |
| `event_type` | TEXT | não | — | — | Tipo do evento | `STOCK_RESERVATION_SUCCEEDED` \| `STOCK_RESERVATION_FAILED` |
| `target` | TEXT | não | — | `orders.events` | Fila destino | `orders.events` |
| `envelope_json` | TEXT | não | — | — | Envelope completo serializado | `{"schema_version":"1.0", ...}` |
| `status` | TEXT | não | IX | `PENDING` | Estado de publicação | `PENDING` \| `SENT` |
| `created_at` | TEXT | não | — | — | Criação | `2026-08-27T12:00:02.910Z` |
| `sent_at` | TEXT | sim | — | `NULL` | Confirmação | `2026-08-27T12:00:02.915Z` |

---

## Domínios / enumerações usadas nas colunas

| Domínio | Valores | Onde |
|---|---|---|
| `OrderStatus` | `PENDING`, `COMPLETED`, `FAILED` | `orders.status` |
| `TaskStatus` | `PENDING`, `DISPATCHED`, `PROCESSING`, `WAITING`, `RETRYING`, `FALLBACK_PROCESSING`, `COMPLETED`, `ABORTED`, `DEAD_LETTERED` | `tasks.status` |
| `Action` | `CONTINUE`, `RETRY`, `WAIT`, `FALLBACK`, `ABORT` | `decisions.proposed_action` / `executed_action` |
| `Target` | `inventory.primary`, `inventory.fallback`, `NULL` | `tasks.current_target`, `decisions.*_target`, `outbox.target` |
| `EventType` | ver [04 §4.6](04-contrato-mensageria.md) | `task_events.event_type`, `processed_events.event_type`, … |
| `ReasonCode` | ver [12 §12.4](12-glossario.md) | `decisions.*_reason_code` |
| `route` | `primary`, `fallback` | `reservations.route` |
| `run_status` | `VALID`, `INVALID`, `NULL` | `executions.run_status` |
| `phase` (execução) | `PILOT`, `EXPERIMENT` | `executions.phase` |
