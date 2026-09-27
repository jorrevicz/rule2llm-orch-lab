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

- Implementado: estrutura do repositório, configuração experimental
  (`config/experiment_config.yml`) e biblioteca comum (`shared/`).
- Planejado: os serviços (`orders-service`, `inventory-service`), a infraestrutura Docker
  Compose e os motores de decisão, a partir do marco M1.

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

Para subir o ambiente com `docker compose up`, é preciso aguardar o marco M1.

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
