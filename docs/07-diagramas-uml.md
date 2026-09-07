# 07 — Diagramas UML

> Notação: Mermaid. Fonte de conteúdo: `TCC_METODOLOGIA.pdf` §4.3 (FIGURA 7, Códigos 3–10),
> §4.4; `piloto-do-experimento.md` §3–22.
>
> Nota sobre casos de uso: o Mermaid não tem diagrama de caso de uso nativo. Usa-se um
> `flowchart` estilizado com fronteira de sistema (atores fora, elipses `([caso de uso])`
> dentro do `subgraph`). Relações `«include»` / `«extend»` aparecem como arestas rotuladas.

Índice:

- [7.1 Casos de uso](#71-casos-de-uso)
- [7.2 Diagrama de classes](#72-diagrama-de-classes)
- [7.3 Diagrama de atividades da mensageria](#73-diagrama-de-atividades-da-mensageria)
- [7.4 Diagramas de sequência](#74-diagramas-de-sequência)

---

## 7.1 Casos de uso

### 7.1.1 Atores

| Ator | Tipo | Papel |
|---|---|---|
| **Cliente / Gerador de carga** | primário | Cria pedidos e consulta status (no experimento, um script de carga) |
| **Pesquisador** | primário | Configura, executa, reseta e mede o experimento |
| **Ollama** | secundário (sistema externo) | Executa a inferência do `LLMDecisionEngine` |
| **RabbitMQ** | secundário (sistema externo) | Transporta as mensagens assíncronas |

### 7.1.2 Diagrama

```mermaid
flowchart LR
    CLIENTE(["👤 Cliente /<br/>Gerador de carga"])
    PESQ(["👤 Pesquisador"])
    OLLAMA(["🖥️ Ollama"])
    RMQ(["🖥️ RabbitMQ"])

    subgraph SYS["Ambiente experimental — rule2llm-orch-lab"]
        UC1(["Criar pedido"])
        UC2(["Consultar status do pedido"])
        UC3(["Processar tarefa de reserva"])
        UC4(["Tomar decisão de orquestração"])
        UC5(["Validar e executar decisão"])
        UC6(["Reservar estoque (primária/fallback)"])
        UC7(["Detectar timeout operacional"])
        UC8(["Registrar rastreabilidade"])

        UC10(["Selecionar motor de decisão (rules|llm)"])
        UC11(["Executar cenário experimental"])
        UC12(["Resetar ambiente"])
        UC13(["Verificar readiness"])
        UC14(["Injetar falha controlada"])
        UC15(["Coletar e consolidar métricas"])
    end

    CLIENTE --> UC1
    CLIENTE --> UC2
    PESQ --> UC10
    PESQ --> UC11
    PESQ --> UC12
    PESQ --> UC13
    PESQ --> UC14
    PESQ --> UC15

    UC1 -. «include» .-> UC3
    UC3 -. «include» .-> UC4
    UC4 -. «include» .-> UC5
    UC5 -. «include» .-> UC6
    UC3 -. «extend» .-> UC7
    UC3 -. «include» .-> UC8
    UC4 -. «extend» .-> UC10

    UC4 --- OLLAMA
    UC5 --- RMQ
    UC6 --- RMQ
    UC11 -. «include» .-> UC13
    UC11 -. «include» .-> UC12
    UC11 -. «extend» .-> UC14
    UC11 -. «include» .-> UC15
```

### 7.1.3 Descrição resumida dos casos de uso

| UC | Ator | Pré-condição | Fluxo principal | Pós-condição |
|---|---|---|---|---|
| Criar pedido | Cliente | Serviço no ar | `POST /orders` com itens → validação → persiste `order` + `task` → `202 Accepted` | `Order = PENDING`, `Task = PENDING` |
| Consultar status | Cliente | Pedido existe | `GET /orders/{id}` → lê `orders.db` | Retorna `PENDING`/`COMPLETED`/`FAILED` |
| Processar tarefa de reserva | (sistema) | `Task` criada | Ciclo `StateBuilder → DecisionEngine → Validator → Executor → RabbitMQ → evento` até estado terminal | `Task` em estado terminal |
| Tomar decisão de orquestração | (sistema) / Ollama | `SYSTEM_STATE` disponível | Rules aplica política **ou** LLM infere; retorna `Decision` | `Decision` registrada em `decisions.jsonl` |
| Validar e executar decisão | (sistema) / RabbitMQ | `Decision` produzida | `DecisionValidator` → `resolve_action` → `DecisionExecutor` publica/agenda | Ação efetivada; `executed_decision` registrada |
| Reservar estoque | (sistema) / RabbitMQ | Mensagem em `inventory.primary`/`inventory.fallback` | Idempotência (`message_id`, `task_id`) → reserva simulada → persiste → publica em `orders.events` | `reservation` persistida; evento publicado |
| Detectar timeout operacional | (sistema) | Dispatch realizado | `timeout_check` após `inventory_timeout_ms`; se a tarefa não avançou → `INVENTORY_TIMEOUT` | Novo ponto de decisão |
| Registrar rastreabilidade | (sistema) | Execução em curso | Grava `states.jsonl`, `task_events.jsonl`, `decisions.jsonl`, logs | Artefatos correlacionáveis por identificadores |
| Selecionar motor de decisão | Pesquisador | Ambiente configurado | Define `decision_engine: rules\|llm` em `experiment_config.yml` | Execução usa o motor escolhido |
| Executar cenário experimental | Pesquisador | Readiness OK | Reset → readiness → warm-up (LLM) → carga → falha → coleta → validação → consolidação | `run_status = VALID\|INVALID`; artefatos gerados |
| Resetar ambiente | Pesquisador | — | Limpa filas/DLQ; restaura os dois SQLite; remove estados transitórios | Estado inicial previsto |
| Verificar readiness | Pesquisador | Containers subindo | Checa containers, filas declaradas, health checks, SQLite inicial, coletores, (LLM) modelo carregado + warm-up | `readiness_status = PASS\|FAIL` |
| Injetar falha controlada | Pesquisador | Cenário de falha | Script Python / controle Docker degrada rota primária, provoca timeout, sobrecarga ou dados inconsistentes | `fault_events.jsonl` registrado |
| Coletar e consolidar métricas | Pesquisador | Execução concluída | Agrega `decisions.jsonl`, `queue_metrics.csv`, `container_stats.csv`, timestamps | `metrics_summary.csv`, `results.csv` |

---

## 7.2 Diagrama de classes

> Camada de coordenação do `orders-service` + domínio + repositórios + mensageria + LLM.
> Métodos e atributos são **conceituais** (o código ainda não existe). `Validator` e
> `Executor` são compartilhados pelas duas abordagens (RNF-009, RNF-010).

```mermaid
classDiagram
    direction LR

    class DecisionEngine {
        <<abstract>>
        +decide(state: SystemState) Decision
    }
    class RulesDecisionEngine {
        -config: RulesConfig
        +decide(state: SystemState) Decision
    }
    class LLMDecisionEngine {
        -client: OllamaClient
        -promptBuilder: PromptBuilder
        -parser: DecisionParser
        +decide(state: SystemState) Decision
    }
    DecisionEngine <|-- RulesDecisionEngine
    DecisionEngine <|-- LLMDecisionEngine

    class StateBuilder {
        -recentEventsLimit: int
        +build(task_id: str) SystemState
    }
    class SystemState {
        +execution_id: str
        +task_id: str
        +state_id: str
        +timestamp: datetime
        +current_event_seq: int
        +task: TaskView
        +service: ServiceView
        +messaging: MessagingView
        +alternatives: AlternativesView
        +recent_events: List~EventView~
    }
    class Decision {
        +action: Action
        +target: Target
        +reason_code: ReasonCode
    }
    class ValidationResult {
        +valid: bool
        +error: str
    }
    class DecisionValidator {
        +validate(decision: Decision, state: SystemState) ValidationResult
    }
    class DecisionExecutor {
        +execute(decision: Decision, state: SystemState) void
        -continue_flow(state) void
        -retry(state, target) void
        -wait(state) void
        -fallback(state, target) void
        -abort(state) void
    }
    class Orchestrator {
        -engine: DecisionEngine
        -builder: StateBuilder
        -validator: DecisionValidator
        -executor: DecisionExecutor
        +handle_decision_point(task_id: str) void
    }

    Orchestrator --> DecisionEngine
    Orchestrator --> StateBuilder
    Orchestrator --> DecisionValidator
    Orchestrator --> DecisionExecutor
    StateBuilder --> SystemState
    DecisionEngine --> Decision
    DecisionValidator --> ValidationResult
    DecisionValidator ..> Decision
    DecisionValidator ..> SystemState
    DecisionExecutor ..> Decision

    class OllamaClient {
        -model: str
        -temperature: float
        -seed: int
        -request_timeout_s: int
        +infer(prompt: str) str
    }
    class PromptBuilder {
        -template: str
        +build(state: SystemState) str
    }
    class DecisionParser {
        +parse(raw: str) Decision
    }
    LLMDecisionEngine --> OllamaClient
    LLMDecisionEngine --> PromptBuilder
    LLMDecisionEngine --> DecisionParser

    class Order {
        +order_id: str
        +status: OrderStatus
        +items: List~OrderItem~
        +created_at: datetime
        +updated_at: datetime
    }
    class OrderItem {
        +sku: str
        +quantity: int
    }
    class Task {
        +task_id: str
        +order_id: str
        +status: TaskStatus
        +phase: str
        +current_target: Target
        +attempt_number: int
        +max_attempts: int
        +wait_count: int
        +max_waits: int
        +fallback_used: bool
        +decision_engine: str
    }
    class MessageEnvelope {
        +schema_version: str
        +execution_id: str
        +message_id: str
        +task_id: str
        +event_type: EventType
        +event_seq: int
        +attempt_number: int
        +published_at: datetime
        +decision_id: str
        +target: Target
        +payload: dict
    }
    class Reservation {
        +reservation_id: str
        +task_id: str
        +order_id: str
        +status: str
        +route: str
        +items: List~ReservationItem~
    }
    class ReservationItem {
        +sku: str
        +quantity: int
    }
    Order "1" o-- "1..*" OrderItem
    Order "1" -- "1" Task
    Reservation "1" o-- "1..*" ReservationItem

    class Publisher {
        +publish(env: MessageEnvelope) void
    }
    class Consumer {
        +on_message(env: MessageEnvelope) void
    }
    class TimeoutCheckTask {
        +run(task_id: str, attempt_number: int) void
    }
    class ReevaluateTask {
        +run(task_id: str) void
    }
    DecisionExecutor --> Publisher
    DecisionExecutor --> TimeoutCheckTask
    Consumer --> Orchestrator

    class OrderRepository
    class TaskRepository
    class TaskEventRepository
    class ProcessedEventRepository
    class ReservationRepository
    class ProcessedMessageRepository
    Orchestrator ..> OrderRepository
    Orchestrator ..> TaskRepository
    StateBuilder ..> TaskEventRepository
    Consumer ..> ProcessedEventRepository
    Consumer ..> ReservationRepository
    Consumer ..> ProcessedMessageRepository

    class Action {
        <<enumeration>>
        CONTINUE
        RETRY
        WAIT
        FALLBACK
        ABORT
    }
    class Target {
        <<enumeration>>
        inventory_primary
        inventory_fallback
        null
    }
    class TaskStatus {
        <<enumeration>>
        PENDING
        DISPATCHED
        PROCESSING
        WAITING
        RETRYING
        FALLBACK_PROCESSING
        COMPLETED
        ABORTED
        DEAD_LETTERED
    }
    class OrderStatus {
        <<enumeration>>
        PENDING
        COMPLETED
        FAILED
    }
    class EventType {
        <<enumeration>>
        ORDER_CREATED
        TASK_CREATED
        STOCK_RESERVATION_REQUESTED
        STOCK_RESERVATION_SUCCEEDED
        STOCK_RESERVATION_FAILED
        INVENTORY_TIMEOUT
        TRANSIENT_ERROR
        WAIT_SCHEDULED
        WAIT_FINISHED
        RETRY_SCHEDULED
        FALLBACK_SCHEDULED
        TASK_COMPLETED
        TASK_ABORTED
        MESSAGE_DEAD_LETTERED
    }
    class ReasonCode {
        <<enumeration>>
        NORMAL_FLOW
        TRANSIENT_RETRY
        PRIMARY_EXHAUSTED
        SERVICE_UNAVAILABLE
        SERVICE_UNAVAILABLE_LIMIT
        QUEUE_PRESSURE
        QUEUE_PRESSURE_LIMIT
        ATTEMPTS_EXHAUSTED
        FALLBACK_FAILED
        INVALID_DATA
        TASK_DEADLINE_EXCEEDED
        UNMAPPED_STATE
        INVALID_DECISION
        LLM_DECISION_TIMEOUT
    }
```

---

## 7.3 Diagrama de atividades da mensageria

O diagrama de atividades da mensageria (raias orders-service / RabbitMQ / inventory-service,
com as duas verificações de idempotência, rota primária/fallback, evento de retorno, corrida
com o `timeout_check` e roteamento para a DLQ) está em
**[04-contrato-mensageria.md §4.8](04-contrato-mensageria.md)**.

Versão compacta (ciclo de vida da tarefa como atividade):

```mermaid
flowchart TD
    START(["POST /orders"]) --> V{"pedido válido?"}
    V -->|não| R400["400 Bad Request"] --> ENDF(["fim"])
    V -->|sim| P["persiste order + task<br/>ORDER_CREATED · TASK_CREATED"]
    P --> DP["StateBuilder → SYSTEM_STATE<br/>(state_id)"]
    DP --> DEC["DecisionEngine.decide()<br/>(decision_id)"]
    DEC --> VAL{"DecisionValidator<br/>valid?"}
    VAL -->|não| AB["executed = ABORT / INVALID_DECISION"]
    VAL -->|sim| ACT{"action?"}
    ACT -->|CONTINUE / RETRY / FALLBACK| PUB["publica envelope na fila do target<br/>agenda timeout_check"]
    ACT -->|WAIT| W["wait_count += 1<br/>aguarda wait_delay_ms"] --> DP
    ACT -->|ABORT| AB
    PUB --> WAITEV["aguarda evento em orders.events<br/>ou INVENTORY_TIMEOUT"]
    WAITEV --> EV{"evento?"}
    EV -->|SUCCEEDED| DONE["Task = COMPLETED<br/>Order = COMPLETED"] --> ENDS(["fim"])
    EV -->|FAILED / TIMEOUT / TRANSIENT| DP
    EV -->|falhas sucessivas de mensageria| DLQ["MESSAGE_DEAD_LETTERED<br/>Task = DEAD_LETTERED · Order = FAILED"] --> ENDF
    AB --> ABDONE["Task = ABORTED<br/>Order = FAILED"] --> ENDF
```

---

## 7.4 Diagramas de sequência

### 7.4.1 Fluxo normal (`POST /orders` → `COMPLETED`)

```mermaid
sequenceDiagram
    autonumber
    actor C as Cliente
    participant API as orders API (FastAPI)
    participant DB as orders.db
    participant SB as StateBuilder
    participant DE as DecisionEngine
    participant VX as Validator + Executor
    participant MQ as RabbitMQ
    participant INV as inventory-service
    participant IDB as inventory.db

    C->>API: POST /orders {items}
    API->>API: validar payload
    API->>DB: INSERT order (PENDING), task (PENDING)
    API-->>C: 202 Accepted {order_id, task_id, status: PENDING}

    API->>SB: build(task_id)
    SB->>DB: ler task + recent_events
    SB-->>DE: SYSTEM_STATE (state_id, phase=PENDING)
    DE-->>VX: Decision {CONTINUE, inventory.primary, NORMAL_FLOW}
    VX->>VX: validate() = valid
    VX->>MQ: publish STOCK_RESERVATION_REQUESTED (msg_id, event_seq=2, attempt=1)
    VX->>MQ: agenda timeout_check (inventory_timeout_ms)
    Note over DB: task DISPATCHED

    MQ->>INV: inventory.primary
    INV->>IDB: message_id em processed_messages? não
    INV->>IDB: task_id em reservations? não
    INV->>IDB: INSERT reservation (RESERVED, route=primary) + processed_messages
    INV->>MQ: publish STOCK_RESERVATION_SUCCEEDED (orders.events)

    MQ->>API: orders.events (evento terminal de sucesso)
    API->>DB: grava processed_events(message_id) e task_events
    API->>DB: task COMPLETED · order COMPLETED
    Note over API,DB: evento terminal de sucesso → não consulta o DecisionEngine
```

### 7.4.2 Timeout → `RETRY`

```mermaid
sequenceDiagram
    autonumber
    participant TC as timeout_check (Celery)
    participant DB as orders.db
    participant SB as StateBuilder
    participant DE as DecisionEngine
    participant VX as Validator + Executor
    participant MQ as RabbitMQ

    Note over TC: dispara após inventory_timeout_ms
    TC->>DB: task avançou nesse attempt_number?
    DB-->>TC: não
    TC->>DB: registra INVENTORY_TIMEOUT (event_seq=4)
    TC->>SB: build(task_id)
    SB-->>DE: SYSTEM_STATE {last_result: timeout, attempt_number: 1, max_attempts: 3}
    DE-->>VX: Decision {RETRY, inventory.primary, TRANSIENT_RETRY}
    VX->>VX: validate() = valid (attempt_number < max_attempts, target == current_target)
    VX->>DB: task RETRYING → attempt_number = 2
    VX->>MQ: publish STOCK_RESERVATION_REQUESTED (msg_id NOVO, event_seq=5, attempt=2)
    VX->>MQ: agenda novo timeout_check (attempt=2)
    Note over DB: task DISPATCHED (2ª tentativa lógica)
```

### 7.4.3 Tentativas esgotadas → `FALLBACK`

```mermaid
sequenceDiagram
    autonumber
    participant SB as StateBuilder
    participant DE as DecisionEngine
    participant VX as Validator + Executor
    participant DB as orders.db
    participant MQ as RabbitMQ
    participant INV as inventory-service

    SB-->>DE: SYSTEM_STATE (last_result=timeout, attempt_number=3, max_attempts=3,<br/>fallback_available=true, fallback_used=false)
    DE-->>VX: Decision (FALLBACK, inventory.fallback, PRIMARY_EXHAUSTED)
    VX->>VX: validate() = valid (fallback_available, not fallback_used, target == inventory.fallback)
    VX->>DB: task FALLBACK_PROCESSING · fallback_used = true
    VX->>MQ: publish STOCK_RESERVATION_REQUESTED para inventory.fallback (msg_id novo, event_seq novo)
    MQ->>INV: inventory.fallback
    INV->>INV: rota fallback (mesmo inventory-service)
    alt reserva efetivada
        INV->>MQ: STOCK_RESERVATION_SUCCEEDED
        Note over DB: task COMPLETED · order COMPLETED
    else fallback falhou
        INV->>MQ: STOCK_RESERVATION_FAILED (last_result = fallback_failed)
        Note over DE: próximo ponto de decisão → ABORT / FALLBACK_FAILED
    end
```

### 7.4.4 Redelivery do broker (idempotência)

```mermaid
sequenceDiagram
    autonumber
    participant MQ as RabbitMQ
    participant INV as inventory-service
    participant IDB as inventory.db
    participant API as orders-service

    MQ->>INV: STOCK_RESERVATION_REQUESTED (message_id = MSG_0192)
    INV->>IDB: message_id em processed_messages? não
    INV->>IDB: task_id em reservations? não
    INV->>IDB: INSERT reservation + processed_messages(MSG_0192)
    INV->>MQ: STOCK_RESERVATION_SUCCEEDED

    Note over MQ,INV: broker reentrega a MESMA mensagem<br/>(mesmo message_id / event_seq / attempt_number, redelivered = true)
    MQ->>INV: STOCK_RESERVATION_REQUESTED (message_id = MSG_0192, redelivered)
    INV->>IDB: message_id em processed_messages? SIM
    INV-->>MQ: reusa resultado — NÃO cria 2ª reserva
    INV->>MQ: reemite STOCK_RESERVATION_SUCCEEDED (idempotente)

    MQ->>API: orders.events (2ª cópia)
    API->>API: message_id em processed_events? SIM → ignora (dedupe)
```

### 7.4.5 Decisão LLM inválida → `ABORT`

```mermaid
sequenceDiagram
    autonumber
    participant SB as StateBuilder
    participant LLM as LLMDecisionEngine
    participant OL as Ollama (llama3.1:8b)
    participant VAL as DecisionValidator
    participant EXE as DecisionExecutor
    participant LOG as decisions.jsonl
    participant DB as orders.db

    SB-->>LLM: SYSTEM_STATE (state_id = STATE_0091)
    LLM->>LLM: prompt fixo + {{SYSTEM_STATE}}
    LLM->>OL: infer(prompt)  (stateless, temperature=0)
    OL-->>LLM: {"action":"RETRY","target":"service_c","reason_code":"RETRY"}
    LLM->>LLM: parse → Decision {RETRY, service_c, RETRY}
    LLM->>VAL: validate(decision, state)
    VAL-->>LLM: {valid: false, error: UNKNOWN_TARGET}
    LLM->>EXE: resolve_action(decision, valid=false)
    EXE->>EXE: executed = Decision {ABORT, null, INVALID_DECISION}
    EXE->>LOG: registra proposed, validation, executed_decision, llm_inference_ms, tokens
    EXE->>DB: task ABORTED · order FAILED
    Note over LOG: run_status = VALID (comportamento da abordagem, permanece na amostra)
```

### 7.4.6 `WAIT` → reavaliação

```mermaid
sequenceDiagram
    autonumber
    participant SB as StateBuilder
    participant DE as DecisionEngine
    participant VX as Validator + Executor
    participant WT as wait timer (Celery)
    participant DB as orders.db

    SB-->>DE: SYSTEM_STATE (queue_size=25 acima do watermark, wait_count=0, max_waits=2)
    DE-->>VX: Decision (WAIT, null, QUEUE_PRESSURE)
    VX->>VX: validate() = valid (target is null)
    VX->>DB: task WAITING · wait_count = 1 (attempt_number inalterado)
    VX->>WT: agenda WAIT_FINISHED após wait_delay_ms
    WT-->>SB: WAIT_FINISHED → build(task_id) de novo
    SB-->>DE: SYSTEM_STATE atualizado
    alt pressão aliviou
        DE-->>VX: Decision (CONTINUE ou RETRY, ...)
        Note over DB: task DISPATCHED
    else ainda sob pressão e wait_count == max_waits
        DE-->>VX: Decision (ABORT, null, QUEUE_PRESSURE_LIMIT)
        Note over DB: task ABORTED · order FAILED
    end
```
