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

## Como usar

Pré-requisitos: Docker em execução, Python 3.12 com o ambiente de
[desenvolvimento](#ambiente-de-desenvolvimento) instalado e, para o LLM, o Ollama.

1. **Inicie o Ollama no host** (só para `--engine LLM`). Ele roda fora do Docker e os
   serviços o acessam por `host.docker.internal:11434`
   ([D-08, docs/03 §3.5](docs/03-stack-tecnologica.md#35-configuração-inicial-do-llm)):

   ```bash
   open -a Ollama                # macOS (no Linux: ollama serve, escutando em 0.0.0.0)
   ollama pull llama3.1:8b       # uma vez
   ```

2. **Rode o teste de sanidade** antes de qualquer execução:

   ```bash
   .venv/bin/python -m pytest
   ```

3. **Inicie o experimento** com `run_experiment` (não é preciso subir o compose antes; o
   script derruba e recria o ambiente a cada execução):

   ```bash
   .venv/bin/python -m scripts.experiment.run_experiment \
       --scenario normal --scenario timeout --engine RULES --engine LLM --repetitions 1
   ```

   Para cada execução, o script:

   1. abre a execução (`PILOT_nnnn`) e grava a config efetiva (base + cenário + motor);
   2. reseta o ambiente (`docker compose down -v`) e o sobe com imagens do commit atual;
   3. verifica a readiness (containers, filas, bancos, coletores, energia do host);
   4. com LLM, faz o warm-up e uma inferência de prova no Ollama e confere que os containers
      do Orders alcançam o runtime;
   5. aplica a carga e a falha do cenário e espera todas as tarefas terminarem;
   6. exporta as métricas do Prometheus e do Ollama e valida a integridade;
   7. se válida, consolida as métricas; se inválida, registra e repete com a mesma config.

   **Resultados:** um diretório por execução em `data/pilot/PILOT_nnnn/` (artefatos de
   rastreabilidade e `metrics_summary.csv`); as inválidas em `data/pilot/invalid_runs.csv`.
   Dados de piloto nunca integram a amostra do TCC (`data/experiment/`, após o
   congelamento — ver [`docs/13-roadmap.md`](docs/13-roadmap.md)).

**Menu interativo** (alternativa aos comandos acima):

```bash
.venv/bin/python menu.py
```

O menu reúne o `run_experiment`, os scripts que ele não automatiza (dataset, schemas, testes
de piloto, bancada do LLM, subir/derrubar o ambiente) e o **protocolo completo ponta a
ponta**: os 7 cenários × Rules e LLM, repetidos N vezes ou continuamente. Cada ciclo é
uma repetição (ciclo k → `repetition_id` k), e a abordagem que abre a repetição alterna
entre ciclos. Ao fim de cada ciclo, o menu confere os artefatos e imprime o resumo. Entre
ciclos há um aviso e uma espera de 10 s, o momento seguro de parar. Ctrl+C durante uma
execução pede **parada segura**: a execução em andamento termina e o laço para. Um 2º
Ctrl+C força a interrupção.

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
menu.py            menu interativo dos procedimentos (protocolo completo, scripts manuais)
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
eval "$(.venv/bin/python -m scripts.pilot.new_execution --engine RULES)"   # ou --engine LLM
docker compose up -d --build --wait                          # serviços gravam em data/pilot/$EXECUTION_ID/
.venv/bin/python -m scripts.pilot.smoke_test --orders 3
.venv/bin/python -m scripts.pilot.collect_artifacts          # consolida os artefatos (antes do reset)
.venv/bin/python -m scripts.pilot.check_traceability         # verifica a cadeia de correlação
```

Com `--engine LLM`, o Ollama precisa estar rodando **no host** (`open -a Ollama`; D-08) com o
modelo `llama3.1:8b` baixado; o script faz readiness e warm-up e registra versão, digest e
quantização. O motor é escolhido por execução: a config efetiva fica no diretório da execução
(`EXECUTION_CONFIG_PATH`, lido pelo compose) e a config base não muda.

### Protocolo experimental (Tabela 14)

```bash
.venv/bin/python -m scripts.experiment.run_experiment \
    --scenario normal --scenario timeout --engine RULES --engine LLM --repetitions 1
```

Por execução: abre, reseta (`down -v`, imagens do commit atual), verifica a readiness
(containers, imagens, filas, bancos, coletores, energia do host), faz o warm-up do LLM,
aplica carga e falha, exporta as métricas do Prometheus e do Ollama, valida a integridade e
consolida (`metrics_summary.csv` e demais). Inválidas vão para `invalid_runs.csv` e são
repetidas com a mesma configuração.

### Servidor da coleta (Linux)

No Linux, o Ollama roda no host como serviço systemd (D-08), e o compose o alcança por
`host.docker.internal` (`host-gateway`). Pontos verificados no servidor Fedora (M8-T25):

- **Ollama:** unit completa em `/etc/systemd/system/ollama.service` (`ExecStart=… serve`,
  `User=ollama`), com `OLLAMA_HOST=0.0.0.0:11434` e `OLLAMA_NUM_PARALLEL=1`. Não use
  `systemctl edit --full` sobre uma unit inexistente: ela fica sem `ExecStart`.
- **Firewall:** com `policy drop` no host, libere a 11434 só para as bridges do Docker
  (`iifname "docker0"` e `iifname "br-*"`, porque o reset recria a rede). Se o arquivo de
  regras começa com `flush ruleset`, aplique com `nft add rule` em vez de recarregar o
  serviço, que apagaria as regras do Docker. A readiness do LLM reprova a execução se os
  containers não alcançarem o runtime.
- **Host dedicado:** nada mais disputando CPU e GPU durante a coleta. No servidor, o k3s foi
  parado (`k3s-killall.sh`) e desabilitado no boot.
- **Permissões:** os containers rodam como root. Antes de um `docker compose up` manual,
  crie `data/pilot/PILOT_0000` como usuário; as execuções do protocolo já criam o diretório.
- **Versões congeladas (M8-T03):** imagens por digest, bibliotecas com o fechamento completo
  e Ollama 0.33.2 com `llama3.1:8b` (digest `46e0c10c…`, Q4_K_M) ([03 §3.8](docs/03-stack-tecnologica.md)).
  A readiness reprova outro runtime, digest ou quantização. Por isso, depois do congelamento, a
  coleta só passa num host com exatamente esse runtime, e o Mac do piloto deixa de servir.
  Recrie o `.venv` da bancada com `uv pip install -r requirements-dev.txt`.
- **Execução longa:** rode o protocolo dentro do `tmux` e traga os dados com
  `rsync -av <servidor>:rule2llm-orch-lab/data/<fase>/ <destino>/`. A numeração `PILOT_nnnn`
  é própria de cada host: não misture os diretórios de hosts diferentes.

### Análise dos resultados

A consolidação por execução roda sozinha ao fim de cada execução válida. A comparação e a
reavaliação offline são etapas posteriores ([protocolo](docs/15-protocolo-de-validacao.md)):

```bash
.venv/bin/python -m scripts.analysis.compare_engines --phase experiment    # results.csv e analysis_summary.md
.venv/bin/python -m scripts.analysis.replay_decisions --phase experiment   # mesmos estados, Ollama ativo
```

`--phase pilot` serve só para verificar o procedimento; o relatório sai marcado como piloto.

### Cenários (carga e falha)

Os cenários estão em `config/scenarios/`: os seis da metodologia (Tabela 12), `normal`,
`overload`, `intermittent_failure`, `timeout`, `inconsistent_data` e `recovery`, e
`persistent_degradation` (D-31).

```bash
eval "$(.venv/bin/python -m scripts.pilot.new_execution --engine RULES --scenario timeout)"
docker compose up -d --build --wait
.venv/bin/python -m scripts.scenarios.run_scenario   # carga + falha + espera + coleta + verificação
```

O executor aplica a carga em malha aberta (`scripts/workload/generate_load.py`) e a janela da
falha (`scripts/faults/injectors.py`): falha intermitente e atraso degradam só a rota primária
(`fault_control.json`, D-20); `recovery` para o container do Inventory inteiro. Ao fim grava
`scenario_summary.json` com pedidos, ações, decisões inválidas e falhas aplicadas.

Artefatos por execução: `execution_metadata.json`, `task_events.jsonl`, `states.jsonl`,
`decisions.jsonl`, `microservices_logs.jsonl`, `fault_events.jsonl` e `workload.jsonl`
(docs/10 §10.2). Dados de piloto nunca integram a amostra.

| Serviço | Papel |
|---|---|
| `rabbitmq` | Broker; topologia declarada em `config/rabbitmq/definitions.json` (UI em `localhost:15672`, usuário `tcc`/`tcc`) |
| `orders-api` | `orders-service` — API HTTP (`localhost:8000`) |
| `orders-worker` | `orders-service` — consome `orders.events` |
| `inventory-worker` | `inventory-service` — um processo por rota: `inventory.primary` e `inventory.fallback` (D-21) |
| `prometheus` | Coleta comum a cada 1 s (`localhost:9090`; D-10, D-26); nunca lida pelo decisor |
| `cadvisor` | CPU/RAM por container |
| `node-exporter` | CPU/RAM do host (no macOS, da VM do Docker Desktop) |

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
