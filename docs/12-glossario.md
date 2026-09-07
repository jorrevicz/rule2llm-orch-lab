# 12 — Glossário

> Fonte: `TCC_METODOLOGIA.pdf` §4.3; `piloto-do-experimento.md` (todo); [`CLAUDE.md`](../CLAUDE.md).

## 12.1 Componentes

| Termo | Definição |
|---|---|
| `orders-service` | Microsserviço de negócio que recebe/valida/coordena o pedido e hospeda a camada de coordenação decisória (FastAPI + Celery + `orders.db`) |
| `inventory-service` | Microsserviço de negócio que realiza a reserva simulada (rota primária ou fallback) e persiste em `inventory.db` (Celery) |
| `StateBuilder` | Módulo que coleta, correlaciona e normaliza sinais em um `SYSTEM_STATE`; atribui `state_id`; seleciona a janela `recent_events`. **Não decide.** |
| `DecisionEngine` | Interface comum `decide(state) -> Decision` |
| `RulesDecisionEngine` | Motor determinístico baseado em política ordenada sobre o `SYSTEM_STATE` (linha de base) |
| `LLMDecisionEngine` | Motor baseado em LLM local (Ollama) que seleciona a ação a partir de prompt fixo + estado (stateless) |
| `DecisionValidator` | Validação determinística da decisão — **o mesmo** para Rules e LLM |
| `DecisionExecutor` | Traduz a decisão validada em operação do ambiente — **não decide** |
| `Orchestrator` | Coordena o ponto de decisão: `StateBuilder → DecisionEngine → DecisionValidator → DecisionExecutor` |
| `timeout_check` | Tarefa Celery interna agendada após cada dispatch; emite `INVENTORY_TIMEOUT` se a tarefa não avançou |
| Ollama | Runtime local que executa o modelo LLM (`llama3.1:8b`) |
| RabbitMQ | Broker de mensagens; exchange `tcc.tasks` (direct) + `tcc.dlx` |
| Celery | Mecanismo de execução de tarefas assíncronas sobre RabbitMQ (não é decisor) |

## 12.2 Identificadores

| Termo | Definição |
|---|---|
| `execution_id` | Identifica uma execução experimental completa (prefixo `EXP_` / `PILOT_`) |
| `task_id` | Identifica uma tarefa; correlaciona todos os seus eventos (`TASK_`) |
| `order_id` | Identifica um pedido (`ORD_`) |
| `message_id` | Identifica uma mensagem; base da idempotência de transporte (`MSG_`) |
| `state_id` | Identifica um snapshot `SYSTEM_STATE` apresentado ao decisor (`STATE_`) |
| `decision_id` | Identifica um ponto de decisão (`DEC_`) |
| `event_seq` | Sequência lógica local por tarefa (`1, 2, 3, …`), monotonicamente crescente. **Não é relógio de Lamport.** |
| `schema_version` | Versão do contrato lógico da mensagem |
| `attempt_number` | Tentativa lógica corrente (a inicial conta; `max_attempts = 3` → inicial + até 2 novas) |

## 12.3 Conceitos-chave

| Termo | Definição |
|---|---|
| `SYSTEM_STATE` | Snapshot normalizado apresentado ao mecanismo decisório — o mesmo para Rules e LLM (ver [06 §6.2](06-modelo-de-decisao.md)) |
| `recent_events` | Janela limitada e ordenada das últimas `K` ocorrências da tarefa (`recent_events_limit`); o histórico completo fica em `task_events.jsonl` |
| Idempotência de transporte | Proteção baseada em `message_id`: uma redelivery não repete o efeito já confirmado |
| Idempotência de negócio | Proteção baseada em `task_id`: uma nova tentativa lógica não produz segunda reserva (`reservations.task_id UNIQUE`) |
| Redelivery | O broker entrega **a mesma** mensagem novamente: mesmo `message_id`, `event_seq`, `attempt_number`; `redelivered = true` |
| `RETRY` (experimental) | Nova **tentativa lógica** decidida pelo orquestrador: novo `message_id`, novo `event_seq`, mesmo `task_id`, `attempt_number + 1`. Nunca delegado ao `autoretry` do Celery |
| Fallback | Rota lógica alternativa **do próprio `inventory-service`** (`inventory.fallback`), não um terceiro serviço |
| Timeout operacional | Orders publicou uma tentativa e não recebeu o evento de conclusão dentro de `inventory_timeout_ms`. **Não** é timeout de HTTP síncrono |
| Ponto de decisão | Evento previamente definido que gera nova consulta ao `DecisionEngine` (ver [06 §6.7](06-modelo-de-decisao.md)) |
| Decisão inválida | Resultado do mecanismo avaliado (JSON malformado, target inexistente, limite excedido, …) → registrar → `ABORT`. **Pode integrar a amostra.** |
| Execução inválida | Falha da bancada (RabbitMQ não subiu, banco não restaurado, Ollama reprovou no readiness, …) → `run_status = INVALID`. **Não integra a amostra.** |
| `steady state` | Comportamento esperado do sistema em execução normal, sem falhas induzidas |
| Overhead decisório | Custo incremental de trocar Rules por LLM para produzir uma ação válida (tempo de decisão, inferência, chamadas, tokens, CPU/RAM) |
| Impacto sistêmico líquido | Efeito ponta a ponta das ações (latência, throughput, taxa de erro, tempo de recuperação, filas) |
| Warm-up | 1 inferência de aquecimento do LLM antes da janela medida; `model_load_ms` e `warmup_inference_ms` não contaminam as métricas das tarefas |
| Readiness | Verificação de prontidão dos componentes antes de iniciar uma execução válida |
| Piloto técnico | Fase de validação de arquitetura/contratos/fluxo; `phase: PILOT`, `eligible_for_sample: false`; **não** integra a amostra |
| Coleta definitiva | Execuções da amostra; `phase: EXPERIMENT`, `eligible_for_sample: true`; configuração congelada |
| `N_rep` | Número de repetições por combinação cenário × abordagem; definido **antes** da coleta |

## 12.4 Ações e `reason_code`

### Ações (espaço executável)

| Ação | Semântica | `target` |
|---|---|---|
| `CONTINUE` | Próxima transição normal prevista | `inventory.primary` |
| `RETRY` | Reexecuta a etapa no mesmo target; `attempt_number += 1`; novo `message_id`/`event_seq` | `current_target` |
| `WAIT` | Aguarda `wait_delay_ms` e reavalia; `wait_count += 1`; não mexe em `attempt_number` | `null` |
| `FALLBACK` | Troca `inventory.primary` → `inventory.fallback`; `fallback_used = true` | `inventory.fallback` |
| `ABORT` | Encerra a tarefa (`Task → ABORTED`, `Order → FAILED`) | `null` |

> `REDIRECT` e `PARALLELIZE` **não** fazem parte da implementação deste recorte.

### `reason_code` — proposta pelo motor

| Código | Ação típica | Origem |
|---|---|---|
| `NORMAL_FLOW` | `CONTINUE` | Fluxo normal (`phase` em `PENDING`/`READY`/`RECOVERED`) |
| `TRANSIENT_RETRY` | `RETRY` | `last_result` em `timeout`/`transient_error` e `attempt_number < max_attempts` |
| `PRIMARY_EXHAUSTED` | `FALLBACK` | Tentativas esgotadas, `fallback_available` e `not fallback_used` |
| `SERVICE_UNAVAILABLE` | `WAIT` | `service_status = unavailable` e `wait_count < max_waits` |
| `SERVICE_UNAVAILABLE_LIMIT` | `ABORT` | `service_status = unavailable` e `wait_count >= max_waits` |
| `QUEUE_PRESSURE` | `WAIT` | `queue_size >= queue_high_watermark` e `wait_count < max_waits` |
| `QUEUE_PRESSURE_LIMIT` | `ABORT` | Pressão de fila e `wait_count >= max_waits` |
| `ATTEMPTS_EXHAUSTED` | `ABORT` | Tentativas esgotadas e sem fallback admissível |
| `FALLBACK_FAILED` | `ABORT` | `last_result = fallback_failed` |
| `INVALID_DATA` | `ABORT` | `last_result = invalid_data` |
| `TASK_DEADLINE_EXCEEDED` | `ABORT` | `elapsed_ms >= task_deadline_ms` |
| `UNMAPPED_STATE` | `ABORT` | Nenhuma regra casou (Rules) |

### `reason_code` / `error` — do executor e do validador

| Código | Contexto |
|---|---|
| `INVALID_DECISION` | `reason_code` da ação `ABORT` executada quando a decisão proposta é inválida |
| `LLM_DECISION_TIMEOUT` | Inferência excedeu `request_timeout_seconds` durante execução válida → `ABORT` |
| `UNKNOWN_ACTION` | Validação: ação fora de `{CONTINUE, RETRY, WAIT, FALLBACK, ABORT}` |
| `UNKNOWN_TARGET` | Validação: target fora de `{null, inventory.primary, inventory.fallback}` |
| `MISSING_REASON_CODE` | Validação: `reason_code` ausente/vazio |
| `RETRY_LIMIT_EXCEEDED` | Validação: `RETRY` com `attempt_number >= max_attempts` |
| `INVALID_RETRY_TARGET` | Validação: `RETRY` com `target != current_target` |
| `FALLBACK_NOT_AVAILABLE` | Validação: `FALLBACK` com `fallback_available = false` |
| `FALLBACK_ALREADY_USED` | Validação: `FALLBACK` com `fallback_used = true` |
| `INVALID_FALLBACK_TARGET` | Validação: `FALLBACK` com `target != inventory.fallback` |
| `TARGET_NOT_ALLOWED` | Validação: `WAIT`/`ABORT` com `target` não nulo |
| `TERMINAL_TASK` | Validação: decisão sobre tarefa em estado terminal |

> Os conjuntos de `reason_code` e `error` devem ser congelados junto com a política do
> `RulesDecisionEngine` e o prompt do LLM antes da coleta definitiva.

## 12.5 Filas e targets

| Nome | Tipo | Papel |
|---|---|---|
| `tcc.tasks` | exchange (direct) | Exchange principal de mensagens de negócio |
| `tcc.dlx` | exchange | Dead-letter exchange |
| `inventory.primary` | fila / routing key | Rota normal de reserva (consumida pelo Inventory) |
| `inventory.fallback` | fila / routing key | Rota alternativa do mesmo Inventory |
| `orders.events` | fila / routing key | Retorno do Estoque + eventos internos de coordenação (consumida pelo Orders) |
| `tasks.dlq` | fila | Mensagens não processáveis (análise experimental) |

## 12.6 Estados

Ver [05-maquina-de-estados.md](05-maquina-de-estados.md).

- **Tarefa:** `PENDING`, `DISPATCHED`, `PROCESSING`, `WAITING`, `RETRYING`,
  `FALLBACK_PROCESSING`, `COMPLETED`\*, `ABORTED`\*, `DEAD_LETTERED`\* (\* = terminal).
- **Pedido:** `PENDING`, `COMPLETED`, `FAILED`.

## 12.7 Parâmetros operacionais

Ver [03-stack-tecnologica.md §3.6](03-stack-tecnologica.md).

`inventory_timeout_ms`, `max_attempts`, `retry_delay_ms`, `wait_delay_ms`, `max_waits`,
`fallback_max_attempts`, `queue_high_watermark`, `recent_events_limit`, `task_deadline_ms`.
