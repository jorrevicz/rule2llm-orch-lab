# 06 — Modelo de decisão

> Fonte: `TCC_METODOLOGIA.pdf` §4.3.2 a §4.3.11 (Tabelas 8–11, Códigos 2–10, Quadro 2);
> `piloto-do-experimento.md` §10–21; [`CLAUDE.md`](../CLAUDE.md) §12–26.

## 6.1 Fluxo de decisão

```
telemetria / eventos
   → StateBuilder            (coleta, correlaciona, normaliza; atribui state_id)
   → SYSTEM_STATE            (mesmo contrato para Rules e LLM)
   → DecisionEngine          (rules OU llm)
   → Decision                (action, target, reason_code)
   → DecisionValidator       (comum; determinístico)
   → resolve_action(...)     (valid → proposed_decision · inválido → ABORT/INVALID_DECISION)
   → DecisionExecutor        (comum; traduz a ação em operação do ambiente)
   → efeitos                 → novo evento relevante → novo SYSTEM_STATE
```

O `StateBuilder` **não** escolhe ação. O `DecisionExecutor` **não** decide. O
`DecisionValidator` é **um só** para as duas abordagens.

## 6.2 `SYSTEM_STATE`

Snapshot normalizado apresentado ao mecanismo decisório. Rules e LLM recebem **o mesmo
objeto**. Schema: `contracts/system_state.schema.json`.

```json
{
  "execution_id": "EXP_0042",
  "task_id": "TASK_0187",
  "state_id": "STATE_0091",
  "timestamp": "2026-08-27T12:00:02.142Z",
  "current_event_seq": 4,

  "task": {
    "phase": "RETRYING",
    "current_service": "inventory-service",
    "current_target": "inventory.primary",
    "attempt_number": 2,
    "max_attempts": 3,
    "wait_count": 0,
    "max_waits": 2,
    "elapsed_ms": 4200
  },

  "service": {
    "status": "degraded",
    "latency_ms": 2054,
    "last_result": "timeout"
  },

  "messaging": {
    "queue_size": 4,
    "redelivered": false
  },

  "alternatives": {
    "fallback_available": true,
    "fallback_used": false,
    "alternative_targets": ["inventory.fallback"]
  },

  "recent_events": [
    { "event_type": "STOCK_RESERVATION_REQUESTED", "attempt_number": 2, "event_seq": 3 },
    { "event_type": "INVENTORY_TIMEOUT",           "attempt_number": 2, "event_seq": 4 }
  ]
}
```

### 6.2.1 Campos (base: metodologia TABELA 8)

| Campo | Tipo | Origem | Função na decisão |
|---|---|---|---|
| `execution_id` | texto | executor experimental | Identificar a execução |
| `task_id` | texto | orquestrador | Correlacionar eventos da mesma tarefa |
| `state_id` | texto | `StateBuilder` | Identificar de forma única o snapshot apresentado ao decisor |
| `timestamp` | data/hora | orquestrador | Horário de construção do snapshot |
| `current_event_seq` | inteiro | histórico da tarefa | Posição lógica atual |
| `task.phase` | categórico | orquestrador | Fase corrente da tarefa |
| `task.current_service` | texto | orquestrador | Serviço físico atual (`inventory-service`) |
| `task.current_target` | texto | orquestrador | Rota lógica atual (`inventory.primary` \| `inventory.fallback`) |
| `task.attempt_number` | inteiro | orquestrador | Tentativas já realizadas |
| `task.max_attempts` | inteiro | configuração | Limite total de tentativas |
| `task.wait_count` | inteiro | orquestrador | Ações `WAIT` já realizadas (distingue 1º WAIT de limite atingido) |
| `task.max_waits` | inteiro | configuração | Limite de esperas |
| `task.elapsed_ms` | número | timestamps | Tempo transcorrido (comparar com `task_deadline_ms`) |
| `service.status` | categórico | serviço/orquestrador | `available` \| `degraded` \| `unavailable` |
| `service.latency_ms` | número | métricas/logs | Degradação de resposta |
| `service.last_result` | categórico | logs da tarefa | `timeout` \| `transient_error` \| `invalid_data` \| `fallback_failed` \| `ok` \| … |
| `messaging.queue_size` | inteiro | RabbitMQ | Pressão/acúmulo de fila |
| `messaging.redelivered` | booleano | mensageria | Reentrega |
| `alternatives.fallback_available` | booleano | configuração do fluxo | Existência de alternativa válida |
| `alternatives.fallback_used` | booleano | histórico/orquestrador | Impedir repetição indefinida do fallback |
| `alternatives.alternative_targets` | lista | configuração do fluxo | Alvos lógicos admissíveis |
| `recent_events` | lista limitada e ordenada | histórico da tarefa | Últimas `K` ocorrências (janela `recent_events_limit`) |

### 6.2.2 Ajustes registrados frente ao texto metodológico

> `piloto-do-experimento.md` §10.2, §33.2–33.3.

- **`alternative_targets`** substitui `alternative_services` (a arquitetura só tem Orders e
  Inventory; não existe `service_c`).
- **`wait_count`** foi adicionado para distinguir "primeiro `WAIT`" de "limite de `WAIT`
  atingido".
- **`fallback_used`** foi adicionado para evitar execução repetida de fallback.
- **`recent_events`** contém somente as últimas `K` ocorrências (`recent_events_limit`); o
  histórico completo permanece em `task_events.jsonl` e **não** é enviado automaticamente ao
  LLM.

### 6.2.3 Controle de contexto

- O `SYSTEM_STATE` é composto **predominantemente por variáveis previamente definidas**, não
  por grandes volumes de log textual.
- O agente observa apenas os **efeitos** operacionais (`degraded`, `timeout`, `queue_size`,
  `attempt_number`) — não o tipo de falha injetada nem o comportamento futuro do cenário
  (metodologia TABELA 9).
- O tamanho e a política de `recent_events` são **iguais** nas duas abordagens (RNF-012).

## 6.3 Espaço de ações

Conjunto executável **restrito a cinco ações** ([`CLAUDE.md`](../CLAUDE.md) §13;
`piloto-do-experimento.md` §11):

```
CONTINUE   RETRY   WAIT   FALLBACK   ABORT
```

`REDIRECT` e `PARALLELIZE` **não** fazem parte da implementação (não há terceiro serviço;
não há subtarefas concorrentes no recorte).

### Semântica exata

| Ação | Efeito | Contadores / regras |
|---|---|---|
| `CONTINUE` | Executa a próxima transição normal prevista (ex.: publicar `STOCK_RESERVATION_REQUESTED` em `inventory.primary`) | `target` = `inventory.primary` |
| `RETRY` | Reexecuta a etapa atual **no mesmo target** | `attempt_number += 1`; novo `message_id`; novo `event_seq`; mesmo `task_id`; `target` = `current_target` |
| `WAIT` | Não envia nova tentativa naquele instante; aguarda `wait_delay_ms`; reconstrói o `SYSTEM_STATE`; consulta de novo o `DecisionEngine` | `wait_count += 1`; **não** incrementa `attempt_number`; `target` = `null` |
| `FALLBACK` | Troca `inventory.primary` → `inventory.fallback` (mesmo `inventory-service`) | `fallback_used = true`; `target` = `inventory.fallback`; admissível só se `fallback_available` e `not fallback_used` |
| `ABORT` | Encerra a tarefa de forma controlada | `Task → ABORTED`; `Order → FAILED`; `target` = `null` |

## 6.4 Contrato de decisão

Rules e LLM retornam **o mesmo schema**. Schema: `contracts/decision.schema.json`.

```json
{ "action": "RETRY", "target": "inventory.primary", "reason_code": "TRANSIENT_RETRY" }
```

| Campo | Obrigatoriedade | Conteúdo |
|---|---|---|
| `action` | obrigatório | `CONTINUE` \| `RETRY` \| `WAIT` \| `FALLBACK` \| `ABORT` |
| `target` | condicional | `inventory.primary` \| `inventory.fallback` \| `null` (em `WAIT`/`ABORT`) |
| `reason_code` | obrigatório | Código categórico associado à decisão (ver [12 §12.4](12-glossario.md)) |
| texto livre | **não permitido** como elemento decisório | — |

A decisão **não** contém comandos de shell, código Python, consultas arbitrárias nem
instruções diretas ao RabbitMQ.

## 6.5 Limitação do fallback

`inventory.fallback` pertence ao **mesmo** `inventory-service`. Portanto:

| Situação | Ações admissíveis |
|---|---|
| Rota primária degradada / falha **localizada** | `RETRY`, `WAIT`, `FALLBACK`, `ABORT` |
| `inventory-service` **totalmente indisponível** | `WAIT`, `ABORT` (o `FALLBACK` não resolve) |

Os scripts de falha devem permitir diferenciar "rota primária degradada" de
"`inventory-service` indisponível" para que `FALLBACK` represente uma ação realmente
executável (`piloto-do-experimento.md` §12, §35.7).

## 6.6 `RulesDecisionEngine` — política ordenada

> Fonte: metodologia Código 4; `piloto-do-experimento.md` §14.3.

Determinístico, explícito, auditável, baseado **somente** no `SYSTEM_STATE`, congelado antes
da coleta definitiva. As regras são avaliadas **em ordem de prioridade**; a primeira que
casa decide.

```python
def decide(state):
    t = state["task"]; s = state["service"]; m = state["messaging"]; a = state["alternatives"]

    # 1. deadline da tarefa
    if t["elapsed_ms"] >= TASK_DEADLINE_MS:
        return Decision("ABORT", None, "TASK_DEADLINE_EXCEEDED")

    # 2. dados inválidos
    if s["last_result"] == "invalid_data":
        return Decision("ABORT", None, "INVALID_DATA")

    # 3. fallback já falhou
    if s["last_result"] == "fallback_failed":
        return Decision("ABORT", None, "FALLBACK_FAILED")

    # 4. serviço indisponível  → WAIT enquanto houver orçamento; senão ABORT
    if s["status"] == "unavailable":
        if t["wait_count"] < MAX_WAITS:
            return Decision("WAIT", None, "SERVICE_UNAVAILABLE")
        return Decision("ABORT", None, "SERVICE_UNAVAILABLE_LIMIT")

    # 5. pressão de fila
    if m["queue_size"] >= QUEUE_HIGH_WATERMARK:
        if t["wait_count"] < MAX_WAITS:
            return Decision("WAIT", None, "QUEUE_PRESSURE")
        return Decision("ABORT", None, "QUEUE_PRESSURE_LIMIT")

    # 6. falha transitória / timeout  → RETRY → FALLBACK → ABORT
    if s["last_result"] in {"timeout", "transient_error"}:
        if t["attempt_number"] < MAX_ATTEMPTS:
            return Decision("RETRY", t["current_target"], "TRANSIENT_RETRY")
        if a["fallback_available"] and not a["fallback_used"]:
            return Decision("FALLBACK", "inventory.fallback", "PRIMARY_EXHAUSTED")
        return Decision("ABORT", None, "ATTEMPTS_EXHAUSTED")

    # 7. fluxo normal
    if t["phase"] in {"PENDING", "READY", "RECOVERED"}:
        return Decision("CONTINUE", "inventory.primary", "NORMAL_FLOW")

    # 8. estado não mapeado
    return Decision("ABORT", None, "UNMAPPED_STATE")
```

Limiares (`config/experiment_config.yml`): `TASK_DEADLINE_MS`, `MAX_ATTEMPTS`, `MAX_WAITS`,
`QUEUE_HIGH_WATERMARK`. Valores do piloto em [03 §3.6](03-stack-tecnologica.md) — **A CONGELAR**.

Não substituir a política por heurísticas novas sem atualizar a especificação; não usar
regras diferentes entre execuções definitivas (RNF-028).

## 6.7 Pontos de decisão

> Fonte: metodologia §4.3.5, TABELA 10.

Um novo ponto de decisão (nova consulta ao `DecisionEngine`) é gerado por eventos previamente
definidos — **não** por leitura contínua de métricas:

| Evento observado | Gera nova decisão? | Ações admissíveis |
|---|:---:|---|
| Início da tarefa | sim | `CONTINUE` |
| Conclusão normal terminal | não | nenhuma |
| Timeout | sim | `RETRY`, `WAIT`, `FALLBACK`, `ABORT` |
| Falha transitória | sim | `RETRY`, `WAIT`, `FALLBACK`, `ABORT` |
| Serviço totalmente indisponível | sim | `WAIT`, `ABORT` |
| Dados inválidos / inconsistentes | sim | `ABORT` |
| Conclusão de nova tentativa | conforme resultado | `CONTINUE`, `RETRY`, `FALLBACK` ou `ABORT` |
| Atualização isolada de métrica sem alteração relevante | não | nenhuma |

Cada ponto de decisão produz um `decision_id`.

## 6.8 `LLMDecisionEngine`

> Fonte: metodologia §4.3.6–4.3.7, Quadro 2; `piloto-do-experimento.md` §18–21.

- Recebe **o mesmo `SYSTEM_STATE`**.
- Monta o **prompt fixo** (`PROMPT FIXO + {{SYSTEM_STATE}}`).
- Faz **uma chamada independente** ao Ollama (sem memória conversacional — RNF-013).
- Exige **resposta estruturada** (JSON com `action`, `target`, `reason_code`).
- Retorna **o mesmo contrato de decisão** do `RulesDecisionEngine`.
- Registra `llm_inference_ms` e, quando disponíveis de forma confiável, `token_usage`.

### Prompt fixo (a congelar antes da coleta — RNF-027)

```
PAPEL
Você atua como mecanismo de decisão de um orquestrador
em um ambiente experimental de microsserviços.

OBJETIVO
Selecionar a próxima ação válida utilizando exclusivamente
o estado operacional fornecido.

AÇÕES PERMITIDAS
- CONTINUE
- RETRY
- WAIT
- FALLBACK
- ABORT

RESTRIÇÕES
- Não inventar serviços, targets ou recursos inexistentes.
- Não ultrapassar o limite máximo de tentativas.
- Utilizar somente alternativas fornecidas no estado.
- Selecionar apenas uma ação pertencente ao conjunto permitido.
- Responder exclusivamente no formato solicitado.

ESTADO OPERACIONAL
{{SYSTEM_STATE}}

FORMATO OBRIGATÓRIO DA RESPOSTA
{
  "action": "<AÇÃO>",
  "target": "<TARGET ou null>",
  "reason_code": "<CÓDIGO>"
}
```

### Segurança de execução (RNF-014)

O modelo **não** produz nem executa comandos arbitrários. O único efeito de uma resposta do
LLM é uma `Decision` estruturada que passa pelo `DecisionValidator` comum.

## 6.9 `DecisionValidator` (comum a Rules e LLM)

> Fonte: metodologia Código 6; `piloto-do-experimento.md` §18, §22.

```python
ALLOWED_ACTIONS = {"CONTINUE", "RETRY", "WAIT", "FALLBACK", "ABORT"}
ALLOWED_TARGETS = {None, "inventory.primary", "inventory.fallback"}

def validate(decision, state):
    if decision.action not in ALLOWED_ACTIONS:
        return invalid("UNKNOWN_ACTION")
    if decision.target not in ALLOWED_TARGETS:
        return invalid("UNKNOWN_TARGET")
    if not decision.reason_code:
        return invalid("MISSING_REASON_CODE")

    if decision.action == "RETRY":
        if state.task.attempt_number >= state.task.max_attempts:
            return invalid("RETRY_LIMIT_EXCEEDED")
        if decision.target != state.task.current_target:
            return invalid("INVALID_RETRY_TARGET")

    if decision.action == "FALLBACK":
        if not state.alternatives.fallback_available:
            return invalid("FALLBACK_NOT_AVAILABLE")
        if state.alternatives.fallback_used:
            return invalid("FALLBACK_ALREADY_USED")
        if decision.target != "inventory.fallback":
            return invalid("INVALID_FALLBACK_TARGET")

    if decision.action in {"WAIT", "ABORT"}:
        if decision.target is not None:
            return invalid("TARGET_NOT_ALLOWED")

    if state.task.phase in {"COMPLETED", "ABORTED", "DEAD_LETTERED"}:
        return invalid("TERMINAL_TASK")

    return valid()
```

Validações mínimas exigidas ([`CLAUDE.md`](../CLAUDE.md) §22): ação permitida; target
permitido; `reason_code` presente; limite de tentativas; target do `RETRY`; disponibilidade
do fallback; fallback ainda não usado; target correto do fallback; ausência de target em
`WAIT`; ausência de target em `ABORT`; impossibilidade de agir sobre estado terminal.

**Não** criar um validador especial para o LLM (RNF-009).

## 6.10 `DecisionExecutor` (comum a Rules e LLM)

> Fonte: metodologia Código 10; `piloto-do-experimento.md` §19, §26.

```python
def resolve_action(decision, valid, error):
    if valid:
        return decision
    return Decision("ABORT", None, "INVALID_DECISION")

class DecisionExecutor:
    def execute(self, decision, state):
        match decision.action:
            case "CONTINUE": return self.continue_flow(state)
            case "RETRY":    return self.retry(state, decision.target)
            case "WAIT":     return self.wait(state)
            case "FALLBACK": return self.fallback(state, decision.target)
            case "ABORT":    return self.abort(state)
            case _:          raise RuntimeError("Validated decision is not executable")
```

Regra: `valid == true` → executa `proposed_decision`; `valid == false` → executa
`ABORT / INVALID_DECISION`. O executor **não** contém política alternativa de decisão
(RNF-010).

## 6.11 Política para decisão inválida

> Fonte: metodologia §4.3.9; `piloto-do-experimento.md` §15–16; [`CLAUDE.md`](../CLAUDE.md) §23.

```
DECISÃO INVÁLIDA  →  registrar  →  ABORT (reason_code = INVALID_DECISION)
```

**Não** realizar: segunda inferência corretiva; autocorreção; fallback automático para
Rules; escolha heurística de ação semelhante; execução parcial. Uma decisão inválida é um
**resultado possível da abordagem**.

Exemplos de decisão inválida: JSON malformado; `action` ausente; `reason_code` ausente; ação
fora do conjunto; `target` inexistente; `RETRY` após `max_attempts`; `FALLBACK` com
`fallback_available = false`; `FALLBACK` após `fallback_used = true`; `CONTINUE` para tarefa
terminal; target incompatível com a ação; serviço/recurso inventado pelo modelo.

Registro (exemplo em `decisions.jsonl`):

```json
{
  "execution_id": "EXP_0042", "task_id": "TASK_0187",
  "state_id": "STATE_0091", "decision_id": "DEC_0091",
  "decision_engine": "llm",
  "proposed_decision": { "action": "RETRY", "target": "service_c", "reason_code": "RETRY" },
  "validation": { "valid": false, "error": "UNKNOWN_TARGET" },
  "executed_decision": { "action": "ABORT", "target": null, "reason_code": "INVALID_DECISION" }
}
```

## 6.12 Decisão inválida **≠** execução experimental inválida

| | Decisão LLM inválida | Falha da bancada experimental |
|---|---|---|
| Natureza | Comportamento do mecanismo avaliado | Problema de infraestrutura |
| Exemplos | Inventou target; ultrapassou limite; JSON inválido; ação incompatível | RabbitMQ não iniciou; banco não restaurado; arquivo corrompido; Ollama reprovou no readiness; config inconsistente |
| Registro | `validation.valid = false`, `executed_decision = ABORT`, `run_status = VALID` | `run_status = INVALID`, `invalid_reason` registrado em `invalid_runs.csv` |
| Amostra | **Pode integrar** a amostra | **Não** integra a amostra; nova execução com a mesma configuração |

## 6.13 Timeout da inferência LLM

| Momento | Consequência |
|---|---|
| **Antes da execução** — Ollama reprova no readiness | `run_status = INVALID` (não inicia repetição válida) |
| **Durante execução válida** — inferência excede `request_timeout_seconds` | `LLM_DECISION_TIMEOUT` → `ABORT`; registrado como comportamento da abordagem (permanece na amostra) |

## 6.14 Realimentação do estado após a ação

Após uma decisão validada ser executada, o processamento **não** é necessariamente
encerrado. Os efeitos da ação são novamente observados pelos coletores e incorporados ao
`SYSTEM_STATE` quando houver novo evento relevante — gerando eventualmente outro ponto de
decisão. A continuidade lógica da tarefa é mantida **externamente ao LLM**, pelo
`StateBuilder`.
