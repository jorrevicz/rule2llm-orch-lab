# rule2llm-orch-lab

Ambiente experimental do TCC **"Orquestração de tarefas assíncronas em microsserviços:
`RulesDecisionEngine` versus agente LLM"**.

O experimento compara dois mecanismos de decisão, que escolhem a próxima ação de
orquestração (`CONTINUE`, `RETRY`, `WAIT`, `FALLBACK`, `ABORT`) em uma arquitetura simulada
com dois microsserviços:

- **`RulesDecisionEngine`**: política determinística, explícita e congelada;
- **`LLMDecisionEngine`**: modelo de linguagem local (Ollama), com prompt fixo e sem memória.

Todo o resto é idêntico nas duas condições: arquitetura, `SYSTEM_STATE`, validador,
executor, instrumentação e cenários.

## Status

O projeto está no **piloto técnico**, e o progresso é acompanhado em
[`docs/13-roadmap.md`](docs/13-roadmap.md).

- Implementado e testado: fluxo normal ponta a ponta (`POST /orders` → RabbitMQ →
  `inventory-service` → evento de retorno → pedido `COMPLETED`), com RabbitMQ, os dois
  serviços e um SQLite por serviço; contrato de mensagens versionado (`contracts/`),
  mensagens fora do contrato na `tasks.dlq`, numeração da trajetória (`event_seq`) e
  idempotência de transporte (`message_id`) e de negócio (`task_id`); `SYSTEM_STATE`
  construído pelo `StateBuilder` e artefatos de rastreabilidade por execução;
  `RulesDecisionEngine`, `DecisionValidator` e `DecisionExecutor` comuns, com as cinco
  ações (`CONTINUE`, `RETRY`, `WAIT`, `FALLBACK`, `ABORT`), timeout operacional e DLQ.
  1º piloto técnico concluído (`PILOT_0012`).
- Planejado: `LLMDecisionEngine` com Ollama (M5), falhas e carga (M6), instrumentação (M7).

Dados de piloto (`data/pilot/`) nunca integram a amostra do TCC (`data/experiment/`).

## Stack

Python · FastAPI · Celery · RabbitMQ · SQLite (um banco por serviço) · Docker Compose ·
Ollama. Detalhes em [`docs/03-stack-tecnologica.md`](docs/03-stack-tecnologica.md).

## Estrutura

```
config/            experiment_config.yml — fonte de verdade dos parâmetros experimentais
contracts/         JSON Schemas (envelope, SYSTEM_STATE, decisão)
services/orders/   orders-service (API, coordenação, StateBuilder, motores de decisão)
services/inventory/ inventory-service (reserva simulada: rota primária e fallback)
shared/            biblioteca comum sem estado (config, IDs, timestamps)
datasets/          dataset de pedidos
scripts/           piloto, carga, falhas, reset, readiness
data/pilot/        artefatos das execuções de piloto (fora da amostra)
data/experiment/   artefatos da coleta definitiva
tests/             unit · integration · contracts
docs/              documentação técnica e metodológica
```

## Ambiente de desenvolvimento

Requer Python 3.12 (a mesma versão prevista para as imagens dos serviços).

```bash
uv venv --python 3.12 .venv            # ou: python3.12 -m venv .venv
uv pip install --python .venv/bin/python -r requirements-dev.txt
.venv/bin/python -m pytest
```

## Ambiente experimental (Docker Compose)

```bash
docker compose up -d --build --wait     # rabbitmq, orders-api, orders-worker, inventory-worker
curl -X POST localhost:8000/orders -H 'content-type: application/json' \
     -d '{"items":[{"sku":"SKU-001","quantity":2}]}'
curl localhost:8000/orders/ORD_000001

.venv/bin/python -m scripts.pilot.smoke_test --orders 3      # smoke test do fluxo normal
.venv/bin/python -m scripts.pilot.duplicate_message_test     # redelivery e nova tentativa
.venv/bin/python -m scripts.pilot.decision_scenarios inventory_paused   # RETRY/FALLBACK/ABORT
.venv/bin/python -m pytest -m integration                    # testes de integração
docker compose down -v                                     # derruba e apaga os bancos
```

### Execução de piloto rastreável

```bash
docker compose down -v                                        # bancos e filas no estado inicial
eval "$(.venv/bin/python -m scripts.pilot.new_execution)"     # abre PILOT_nnnn e grava os metadados
docker compose up -d --build --wait                          # serviços gravam em data/pilot/$EXECUTION_ID/
.venv/bin/python -m scripts.pilot.smoke_test --orders 3
.venv/bin/python -m scripts.pilot.collect_artifacts          # consolida os artefatos (antes do reset)
.venv/bin/python -m scripts.pilot.check_traceability         # verifica a cadeia de correlação
```

Artefatos por execução: `execution_metadata.json`, `task_events.jsonl`, `states.jsonl`,
`decisions.jsonl` e `microservices_logs.jsonl` (docs/10 §10.2). Dados de piloto nunca
integram a amostra.

| Serviço | Papel |
|---|---|
| `rabbitmq` | Broker; topologia declarada em `config/rabbitmq/definitions.json` (UI em `localhost:15672`, usuário `tcc`/`tcc`) |
| `orders-api` | `orders-service` — API HTTP (`localhost:8000`) |
| `orders-worker` | `orders-service` — consome `orders.events` |
| `inventory-worker` | `inventory-service` — consome `inventory.primary` |

`orders-api` e `orders-worker` são o mesmo microsserviço e compartilham `orders.db`;
o `inventory-service` tem o seu próprio `inventory.db`.

## Documentação

O índice completo está em [`docs/README.md`](docs/README.md). Pontos de partida:

- [Visão geral](docs/01-visao-geral.md): objetivo, variável experimental, escopo.
- [Arquitetura](docs/02-arquitetura.md): os dois microsserviços e a camada de coordenação.
- [Modelo de decisão](docs/06-modelo-de-decisao.md): `SYSTEM_STATE`, ações, Rules,
  Validator, Executor, LLM.
- [Roadmap](docs/13-roadmap.md): marcos, tasks e progresso.
- [`docs/ref/piloto-do-experimento.md`](docs/ref/piloto-do-experimento.md): decisões de
  implementação do piloto.

As regras de trabalho para agentes de IA neste repositório estão em [`CLAUDE.md`](CLAUDE.md).
