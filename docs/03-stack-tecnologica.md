# 03 — Stack tecnológica (arquitetura definida)

> Fonte: `TCC_METODOLOGIA.pdf` §4.1, §4.3, §4.3.7; `piloto-do-experimento.md` §2, §19–20, §31, §34;
> [`CLAUDE.md`](../CLAUDE.md) §5, §33–34.

## 3.1 Legenda de status

| Status | Significado |
|---|---|
| **CONFIRMADO** | Previsto no delineamento/metodologia ou confirmado pelo projeto |
| **DECISÃO DE IMPLEMENTAÇÃO** | Escolha concreta adotada para tornar a arquitetura implementável |
| **A CONGELAR** | Pode ser ajustado durante o piloto; deve ser definido e versionado antes da coleta definitiva |

## 3.2 Stack de execução

| Elemento | Tecnologia | Status | Função no experimento |
|---|---|---|---|
| Linguagem | **Python** | CONFIRMADO | Serviços, orquestração, scripts de carga/falha, coleta |
| API HTTP | **FastAPI** | CONFIRMADO | Interface HTTP do `orders-service` (`POST /orders`, consulta de status, health check) |
| Containerização | **Docker** | CONFIRMADO | Isolamento de cada componente |
| Composição local | **Docker Compose** | CONFIRMADO | Subida padronizada e reprodutível do ambiente |
| Broker de mensagens | **RabbitMQ** | CONFIRMADO | Comunicação assíncrona Orders ↔ Inventory; filas primária/fallback/eventos/DLQ |
| Execução assíncrona | **Celery** | CONFIRMADO pelo projeto | Workers e tarefas Python sobre RabbitMQ; dispatch e agendamento interno (timeout-check) |
| Persistência `orders-service` | **SQLite** (`orders.db`) | CONFIRMADO | Banco exclusivo do serviço de pedidos |
| Persistência `inventory-service` | **SQLite** (`inventory.db`) | CONFIRMADO | Banco exclusivo do serviço de estoque |
| Linha de base decisória | **`RulesDecisionEngine`** | CONFIRMADO | Decisão determinística baseada em regras |
| Runtime LLM | **Ollama** local | CONFIRMADO pelo projeto | Execução local do modelo para o `LLMDecisionEngine` |
| Modelo LLM inicial | **`llama3.1:8b`** | DECISÃO DE IMPLEMENTAÇÃO | Modelo inicial do piloto (pode mudar durante o piloto por inviabilidade objetiva de hardware) |
| Instrumentação | logs estruturados JSONL, **Docker Stats**, métricas **RabbitMQ**, **Prometheus** | CONFIRMADO na metodologia | Coleta experimental comum às duas abordagens |
| Injeção de falhas | scripts **Python** + controle do **Docker Compose** | CONFIRMADO | Perturbações controladas e reprodutíveis |

## 3.3 Papel e limites de cada tecnologia

### FastAPI
- **Faz:** expõe a interface HTTP do `orders-service`; recebe `POST /orders`; devolve
  `202 Accepted` com `order_id`, `task_id`, `status`.
- **Não faz:** não realiza chamada HTTP síncrona entre `orders-service` e `inventory-service`
  para a etapa de negócio (essa coordenação é sempre assíncrona via RabbitMQ).

### RabbitMQ
- **Faz:** intermedia toda a comunicação assíncrona; suporta filas distintas para rota
  primária, fallback e eventos de retorno; permite observar mensagens pendentes; permite
  identificar reentregas; encaminha mensagens não processáveis para a DLQ; fornece sinais
  que compõem o `SYSTEM_STATE` (`queue_size`, `redelivered`).
- **Não faz:** **não decide** políticas de orquestração.

### Celery
- **Faz:** transporta e executa tarefas assíncronas decididas pelo orquestrador; agenda a
  verificação interna de timeout após cada dispatch.
- **Não faz:** **não é o decisor**. `autoretry_for` / `retry` automático do Celery **não** é
  usado para implementar o `RETRY` experimental — o `RETRY` é uma nova tentativa lógica
  decidida pelo `DecisionEngine` (ver [04 §4.5](04-contrato-mensageria.md)).

### SQLite
- **Faz:** persistência independente de cada microsserviço.
- **Não faz:** não há banco compartilhado; um serviço nunca acessa o banco do outro
  (RNF-003).

### Ollama
- **Faz:** runtime local que carrega `llama3.1:8b` e responde a chamadas de inferência do
  `LLMDecisionEngine`.
- **Não faz / não pode:** o modelo **não** executa shell, Docker, RabbitMQ, SQLite, arquivos
  do host, comandos arbitrários nem código Python gerado dinamicamente. O fluxo é sempre:
  `SYSTEM_STATE → prompt fixo → LLM → decisão estruturada → DecisionValidator → DecisionExecutor`
  (RNF-014).

### Prometheus / Docker Stats / métricas RabbitMQ
- **Faz:** instrumentação **comum** às duas abordagens; alimenta as métricas agregadas
  calculadas após ou durante as execuções.
- **Não faz:** não fornece informação operacional adicional a Rules ou a LLM; indicadores
  agregados (P95, estatísticas consolidadas) **não** são visíveis ao orquestrador durante a
  execução (RNF-029).

## 3.4 Fora da execução

| Tecnologia | Situação |
|---|---|
| **Apache Airflow** | Só no referencial conceitual do TCC. **Não** adicionar ao `docker-compose`; **não** usar como baseline; **não** substituir `RulesDecisionEngine` por Airflow. |
| Kubernetes, service mesh, API gateway, Consul, etcd | Fora do escopo (RNF-030). |
| Redis, PostgreSQL, Kafka, Temporal, Elasticsearch, banco vetorial, RAG | Ausência intencional. Perguntar antes de introduzir ([`CLAUDE.md`](../CLAUDE.md) §33). |
| LangGraph, AutoGen, frameworks multi-agente | Fora do escopo. |
| Chaos Mesh / LitmusChaos | Fora do escopo; falhas por scripts próprios. |

## 3.5 Configuração inicial do LLM

> Fonte: `piloto-do-experimento.md` §19–20; metodologia §4.3.7, Quadro 2.

```yaml
llm:
  runtime: ollama
  runtime_version: null            # A CONGELAR
  model: llama3.1:8b               # DECISÃO DE IMPLEMENTAÇÃO (pode mudar durante o piloto)
  model_digest: null               # A CONGELAR (digest/identificador real do modelo)
  quantization: null               # A CONGELAR (quantização efetivamente carregada)
  temperature: 0.0
  top_p: 1.0
  max_tokens: 64
  seed: 42                         # só como controle efetivo se o runtime realmente suportar
  request_timeout_seconds: 30
  session_memory: false            # LLM stateless entre pontos de decisão
  stream: false
```

Regras (RNF-013, RNF-026, RNF-027):

- **Stateless:** cada decisão reconstrói o prompt (`PROMPT FIXO + SYSTEM_STATE atual`); sem
  histórico conversacional implícito do runtime.
- **Warm-up:** 1 inferência de aquecimento antes da janela medida; registrar `model_load_ms`
  e `warmup_inference_ms`; a inferência de warm-up **não** contamina as métricas das tarefas.
- **Mudança de modelo:** permitida durante o piloto por inviabilidade objetiva de
  hardware/runtime; depois do congelamento, `modelo`, `quantização`, `runtime`, `versão`,
  `parâmetros` e `prompt` permanecem constantes.

## 3.6 Parâmetros operacionais (fonte de verdade: `config/experiment_config.yml`)

> Fonte: `piloto-do-experimento.md` §14.1, §31, §40; metodologia Código 11.
> Valores do **piloto** — provisórios, **A CONGELAR** antes da coleta definitiva.

```yaml
messaging:
  inventory_timeout_ms: 2000       # timeout operacional (não é timeout de HTTP síncrono)
  max_attempts: 3                  # 1 tentativa inicial + até 2 novas (ver §3.7)
  retry_delay_ms: 500
  wait_delay_ms: 1000
  max_waits: 2
  fallback_max_attempts: 1
  queue_high_watermark: 20         # provisório; valor final após observar o steady state

context:
  recent_events_limit: 5           # janela K de recent_events; A CONGELAR

llm:
  # ver §3.5

workload:
  dataset: datasets/orders_v1.json
  requests: null                   # A CONGELAR
  rate_per_second: null            # A CONGELAR
  seed: null                       # A CONGELAR

fault:
  type: none                       # none | overload | intermittent_error | timeout | inconsistent_data | recovery
  seed: null
```

Parâmetros que **não** devem permanecer indefinidos antes da coleta definitiva:
`inventory_timeout_ms`, `max_attempts`, `retry_delay_ms`, `wait_delay_ms`, `max_waits`,
`queue_high_watermark`, `recent_events_limit`, `task_deadline_ms`, modelo LLM e digest,
`repetitions` (`N_rep`), `execution_order`, seeds (`seed_workload`, `seed_fault`, `seed_llm`).

## 3.7 `max_attempts` (não `max_retries`)

```
max_attempts = 3   →   1 tentativa inicial  +  no máximo 2 novas tentativas
```

A tentativa inicial conta dentro do limite. Evita interpretar "3 retries" como 4 tentativas
totais. Usar sempre `max_attempts`; nunca `max_retries`.

## 3.8 Versões a congelar

> Nunca inventar versões não verificadas ([`CLAUDE.md`](../CLAUDE.md) §44). Campos
> desconhecidos ficam `null` até serem obtidos de forma confiável.

Antes da coleta definitiva, registrar em `execution_metadata.json`: versão exata do Python;
versões das bibliotecas; versões/imagens Docker; versão do RabbitMQ; versão do Celery;
versão do Ollama; digest/identificador do modelo; quantização efetiva; hardware (CPU, RAM,
GPU); hashes de `experiment_config.yml` e do dataset; `git_commit`.
