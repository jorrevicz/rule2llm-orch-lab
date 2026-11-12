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
- **Como (D-10, D-26):** Prometheus no compose coleta a cada 1 s o cAdvisor (CPU/RAM por
  container), o node-exporter (host) e o plugin `rabbitmq_prometheus` (métricas por fila);
  ao fim de cada execução, `queue_metrics.csv` e `container_stats.csv` são extraídos da API
  do Prometheus. O Ollama roda no host (D-08) e fica fora do cAdvisor: CPU/RAM do processo
  vêm de um amostrador próprio (`ps`, 1 s), na mesma tabela. O `StateBuilder` continua lendo
  a fila por `queue.declare` passivo, não pelo Prometheus.

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
  runtime_version: "0.33.2"        # M8-T03: lido do servidor da coleta; a readiness exige igual
  model: llama3.1:8b
  model_digest: 46e0c10c039e019119339687c3c1757cc81b9da49709a3b3924863ba87ca666e
  quantization: Q4_K_M
  temperature: 0.0
  top_p: 1.0
  max_tokens: 64
  seed: 42                         # só como controle efetivo se o runtime realmente suportar
  request_timeout_seconds: 30
  session_memory: false            # LLM stateless entre pontos de decisão
  stream: false
```

Parâmetros acrescentados no M5 (A CONGELAR): `response_format: json` (D-09), `num_ctx`
(contexto explícito — o Ollama corta em silêncio um prompt maior que o contexto) e
`keep_alive` (o modelo não é descarregado no meio de uma execução).

**D-08 (2026-09-29) 🔬:** o Ollama roda **no host** (bare-metal, GPU Metal do Apple M2), fora do Docker Compose; os serviços o acessam por `host.docker.internal:11434`. Em container, o Docker no macOS só teria CPU. Consequência para a instrumentação: o custo de CPU/RAM do modelo **não aparece no Docker Stats**; o processo do Ollama precisa de coleta própria no host (M7-T04), para que o overhead do LLM não fique invisível na comparação de custo.

**D-09 (2026-09-29) 🔬:** chamada com `format: "json"`: o runtime restringe a saída a JSON **sintaticamente** válido, sem schema. Ação, target e `reason_code` continuam livres, e as decisões inválidas de conteúdo seguem sendo medidas pelo `DecisionValidator`. Um schema restrito (enum de ações/targets) eliminaria na geração parte do comportamento avaliado.

**Observado no piloto (M5):** o Ollama reaproveita em cache o prefixo de prompt já avaliado
(o template fixo). Isso reduz o tempo de avaliação do prompt quando chamadas consecutivas
compartilham o prefixo, sem mudar a resposta nem constituir memória de conversa (cada chamada
continua independente). A latência passa a depender da ordem das chamadas, o que vale igualmente
para todas as execuções LLM; `prompt_eval_count` continua informando o tamanho total do prompt.

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
> Valores **finais** de M8-T02 (D-11), congelados em `freeze-v1`.

```yaml
messaging:
  inventory_timeout_ms: 2000       # timeout operacional (não é timeout de HTTP síncrono)
  max_attempts: 3                  # 1 tentativa inicial + até 2 novas (ver §3.7)
  retry_delay_ms: 500
  wait_delay_ms: 1000
  max_waits: 2
  fallback_max_attempts: 1
  queue_high_watermark: 20         # entre o steady state (fila ≤ 1) e o pico da rajada de overload

context:
  recent_events_limit: 5           # janela K de recent_events

llm:
  # ver §3.5

inventory:                         # D-22, D-03
  service_time_ms: 100             # tempo de serviço simulado por solicitação (duas rotas)
  catalog: datasets/inventory_catalog_v1.json

workload:                          # cenário normal; os demais em config/scenarios/
  dataset: datasets/orders_v1.json
  requests: 60
  rate_per_second: 0.25            # malha aberta: taxa fixa de envio (D-11)
  seed: 1042                       # seed que gerou o dataset (seed_workload)

fault:                             # metodologia Código 11; D-20
  type: none                       # none | overload | intermittent_error | timeout | inconsistent_data | recovery
  target: null                     # inventory.primary | inventory-service | null
  start_after_seconds: null        # início da janela, a partir do início da carga
  duration_seconds: null
  failure_probability: null        # intermittent_error, timeout, inconsistent_data
  delay_ms: null                   # timeout: atraso na rota primária
  overload_rate_per_second: null   # overload: taxa de envio durante a janela
  seed: null                       # seed_fault
```

Cada cenário é um arquivo `config/scenarios/<cenário>.yml` com as seções `workload` e
`fault`; `new_execution --scenario` grava a config efetiva (base + cenário) no diretório
da execução (M6-T06). Mesmo arquivo para Rules e LLM no mesmo cenário. A seed de falha
efetiva é a do cenário somada a `repetition_id − 1` (D-27): o par Rules/LLM usa a mesma, e
repetições diferentes sorteiam falhas diferentes. Fora da sobrecarga, a taxa de carga é
calibrada para não saturar o runtime do LLM (D-30 e D-37, [15 §15.4](15-protocolo-de-validacao.md)).

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

## 3.8 Versões congeladas (M8-T03)

> Nunca inventar versões não verificadas ([`CLAUDE.md`](../CLAUDE.md) §44). Todos os valores
> abaixo foram lidos do servidor da coleta em 2026-11-11.

| Componente | Versão | Onde está fixada |
|---|---|---|
| Imagem base dos serviços | `python:3.12-slim@sha256:5e2bdb1b…` (Python 3.12.16, Debian 13.7, pip 25.0.1) | `services/*/Dockerfile` |
| Bibliotecas dos serviços | Fechamento completo do `pip freeze` (FastAPI 0.162.4, Uvicorn 0.54.0, Celery 5.7.0, kombu 5.7.0, Pydantic 2.14.3, PyYAML 6.0.3) | `services/*/requirements.txt` |
| RabbitMQ | `rabbitmq:4.4.0-management@sha256:af6f50d3…` (4.4.0) | `docker-compose.yml` |
| Prometheus, cAdvisor, node-exporter | v3.15.0, v0.55.1, v1.12.1, cada um por digest | `docker-compose.yml` |
| Bancada no host | Python 3.12.15; pytest 9.2.0, httpx2 2.16.0, scipy 1.18.1, numpy 2.5.4, psutil 7.2.2 | `requirements-dev.txt` |
| Runtime e modelo | Ollama 0.33.2; `llama3.1:8b`, digest `46e0c10c…`, Q4_K_M; `OLLAMA_NUM_PARALLEL=1` | `config/experiment_config.yml` (verificado pela readiness); unit do systemd (README) |
| Host | Fedora 44, Xeon W-2223, 15,3 GB, RTX 3080 10 GB (driver 620.70.06), Docker 29.8.2 | `execution_metadata.json` de cada execução |

Os digests são índices multiarquitetura: a mesma referência serve no servidor (amd64) e no Mac
(arm64). A tag flutuante `rabbitmq:4-management` já apontava para outra imagem no registro quando
o pin foi feito. O `OLLAMA_NUM_PARALLEL` não é exposto pela API do Ollama, por isso fica no
procedimento de instalação do servidor e não na readiness.

Cada execução continua registrando em `execution_metadata.json` o que encontrou: Python,
Celery, RabbitMQ, imagens, Ollama, digest, quantização, hardware, hashes da config e do
dataset e `git_commit`.
