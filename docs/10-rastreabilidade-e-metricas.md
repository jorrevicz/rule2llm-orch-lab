# 10 — Rastreabilidade e métricas

> Fonte: `TCC_METODOLOGIA.pdf` §4.3.10, §4.5 (Tabelas 15–18, equações 4.1–4.13, Códigos 7–9, 12);
> `piloto-do-experimento.md` §25–28; [`CLAUDE.md`](../CLAUDE.md) §27–28, §36–37, §42.

## 10.1 Correlação de identificadores

Deve ser sempre possível correlacionar:

```
execution_id  →  task_id  →  state_id  →  decision_id
                     ↳  message_id  ↳  event_seq
```

| Identificador | Prefixo | Emitido por | Escopo |
|---|---|---|---|
| `execution_id` | `EXP_` / `PILOT_` | executor experimental | Uma execução completa (cenário × abordagem × repetição) |
| `task_id` | `TASK_` | orders-service | Uma tarefa; correlaciona todos os seus eventos |
| `state_id` | `STATE_` | `StateBuilder` | Um snapshot apresentado ao decisor |
| `decision_id` | `DEC_` | orquestrador | Um ponto de decisão |
| `message_id` | `MSG_` | produtor da mensagem | Uma mensagem (base da idempotência de transporte) |
| `event_seq` | inteiro | aplicação, por tarefa | Sequência lógica local (não é relógio de Lamport) |
| `order_id` | `ORD_` | orders-service | Um pedido |

## 10.2 Artefatos de coleta por execução

> Fonte: `piloto-do-experimento.md` §25; metodologia Quadro 3, Quadro 4, TABELA 16.
> A árvore fica em `data/<pilot|experiment>/<EXECUTION_ID>/`.

| Arquivo | Formato | Conteúdo | Pergunta que responde |
|---|---|---|---|
| `execution_metadata.json` | JSON | Config efetiva, versões, seeds, hardware, readiness, `run_status` | "Sob qual configuração isso rodou?" |
| `initial_state.json` | JSON | Estado inicial restaurado (bancos, filas) | "O ambiente partiu do estado esperado?" |
| `states.jsonl` | JSONL | `SYSTEM_STATE` integral + `recent_events` efetivamente enviados | "O que o decisor sabia naquele instante?" |
| `task_events.jsonl` | JSONL | Trajetória completa de cada tarefa | "Como a tarefa evoluiu?" |
| `decisions.jsonl` | JSONL | Proposta, validação, ação executada, tempos, tokens | "O que foi proposto, validado e executado?" |
| `fault_events.jsonl` | JSONL | Tipo, início, duração, fim de cada falha injetada | "Qual perturbação foi aplicada e quando?" |
| `microservices_logs.jsonl` | JSONL | Eventos internos e erros dos serviços | "O que os serviços registraram?" |
| `queue_metrics.csv` | CSV | Fila, reentrega, rejeição, DLQ ao longo do tempo | "Como as filas se comportaram?" |
| `container_stats.csv` | CSV | CPU, RAM, I/O por container | "Qual foi o custo computacional?" |
| `invalid_runs.csv` | CSV | `execution_id`, cenário, abordagem, motivo da invalidação | "Quais execuções não entraram na amostra e por quê?" |

Consolidação (pós-execução): `metrics_summary.csv`, `latency_metrics.csv`,
`throughput_metrics.csv`, `error_metrics.csv`, `recovery_metrics.csv`, `blast_radius.csv`,
`results.csv`, `analysis_summary.md`.

## 10.3 Campos dos artefatos JSONL principais

### `states.jsonl`

```
execution_id · task_id · state_id · timestamp · current_event_seq
system_state (SYSTEM_STATE integral — ver 06 §6.2)
recent_events (janela K efetivamente enviada)
```

### `task_events.jsonl`

```
execution_id · task_id · message_id · event_seq · event_type
service · target · timestamp · redelivered · attempt_number
```

### `decisions.jsonl`

> Metodologia Códigos 7–9. `decision_engine` = `RULES` ou `LLM`. `llm_inference_ms` e
> `token_usage` só para `LLM` (nulos para `RULES`).

```json
{
  "execution_id": "EXP_0042",
  "task_id": "TASK_0187",
  "state_id": "STATE_0091",
  "decision_id": "DEC_0091",
  "decision_engine": "LLM",
  "proposed_decision": { "action": "RETRY", "target": "inventory.primary", "reason_code": "TRANSIENT_RETRY" },
  "validation": { "valid": true, "error": null },
  "executed_decision": { "action": "RETRY", "target": "inventory.primary" },
  "decision_time_ms": 684,
  "llm_inference_ms": 642,
  "token_usage": { "input_tokens": 428, "output_tokens": 21, "total_tokens": 449 },
  "timestamp": "2026-08-27T12:00:02.196Z"
}
```

Indicadores derivados de `decisions.jsonl` (não precisam ser gravados por linha):
`decision_count`, `llm_call_count`, `total_llm_inference_ms`, `total_input_tokens`,
`total_output_tokens`, `total_tokens`. Opcional:
`state_age_ms = decision_timestamp − state_timestamp`.

### `execution_metadata.json`

> Metodologia Código 12. Campos desconhecidos permanecem `null` — **não inventar**.

```
execution_id · phase · eligible_for_sample · scenario_id · repetition_id · decision_engine
experiment_version · git_commit
scenario_config · scenario_config_hash · dataset · dataset_hash
seeds { workload · fault · llm }
software { python_version · docker_compose_version · rabbitmq_version · llm_runtime · llm_runtime_version }
llm { model · model_digest · quantization · temperature · top_p · max_tokens · seed · request_timeout_seconds · session_memory · stream }
hardware { cpu · ram_gb · gpu }
context_policy { recent_events_limit · llm_session_memory }
message_ordering_policy { scope: per_task · field: event_seq · global_total_order: false · logical_clock: false }
readiness_status · model_load_ms · warmup_inference_ms
run_status · invalid_reason
```

## 10.4 Métricas

> Fonte: metodologia §4.5, Tabelas 15 e 17; `piloto-do-experimento.md` §42.

### 10.4.1 Custo decisório (**overhead**) × impacto sistêmico

A análise **separa** o custo diretamente associado ao mecanismo de decisão dos efeitos que
suas ações produzem no sistema:

- **Overhead decisório** — custo incremental de trocar Rules por LLM para produzir uma ação
  válida, mantendo estado, validação e executor equivalentes. Observado por: tempo de
  decisão, tempo acumulado de inferência, nº de chamadas ao modelo, tokens, CPU/RAM.
- **Impacto sistêmico líquido** — efeito ponta a ponta: latência, throughput, taxa de erro,
  tempo de recuperação, comportamento das filas.

> Uma decisão LLM pode ter overhead decisório **positivo** e, ao mesmo tempo, **reduzir** a
> latência total do fluxo. `Δ_E2E` **não** deve ser confundido com overhead.
> Não é criado um *score* único que combine as dimensões com pesos arbitrários.

### 10.4.2 Fórmulas (metodologia §4.5.1)

| Métrica | Definição |
|---|---|
| Tempo de decisão do ponto `k` | `T_dec,k = t_acao_pronta,k − t_estado_pronto,k` |
| Tempo decisório acumulado | `T_dec,total = Σ_k T_dec,k` |
| Tempo acumulado de inferência (LLM) | `T_inf,total = Σ_k T_inf,k` |
| Tempo médio por decisão | `T_dec,medio = T_dec,total / N_dec` |
| Overhead decisório absoluto (por cenário `s`) | `OH_dec(s) = mean(T_dec,total(L,s)) − mean(T_dec,total(R,s))` |
| Overhead decisório relativo | `OH_dec%(s) = [mean(T_dec,total(L,s)) − mean(T_dec,total(R,s))] / mean(T_dec,total(R,s)) × 100` |
| Chamadas ao LLM por tarefa | `Calls_task = N_LLM_calls / N_tasks` |
| Tokens totais | `Tokens_total = Σ (input_tokens + output_tokens)` |
| Tokens por tarefa | `Tokens_task = Tokens_total / N_completed_tasks` |
| Impacto sistêmico | `Δ_E2E(s) = mean(T_E2E(L,s)) − mean(T_E2E(R,s))` ; `Δ_Thr(s) = mean(Thr(L,s)) − mean(Thr(R,s))` |
| Diferença genérica por métrica `Y` | `Δ_Y(s) = mean(Y(L,s)) − mean(Y(R,s))` |
| CPU/RAM acumulados (opcional) | `CPU_AUC ≈ Σ_j CPU_j · Δt_j` ; `RAM_AUC ≈ Σ_j RAM_j · Δt_j` |

`R` = abordagem baseada em regras; `L` = abordagem baseada em LLM; `s` = cenário; `k` = ponto
de decisão. `T_dec(R) ≈ T_rules + T_validation`;
`T_dec(L) ≈ T_prompt + T_inference + T_parsing + T_validation`. O `StateBuilder` **não** entra
na decomposição do overhead (é compartilhado). Medidas obrigatórias:
`decision_time_ms` e `llm_inference_ms`.

Interpretação de `Δ`: para latência e tempo de recuperação, `Δ < 0` favorece LLM; para
throughput, `Δ > 0` favorece LLM.

### 10.4.3 Métricas coletadas (metodologia TABELA 15)

| Métrica | O que mede | Arquivo de saída |
|---|---|---|
| Latência média | Tempo médio entrada→conclusão da tarefa | `latency_metrics.csv` |
| Latência P95 | Tempo abaixo do qual 95% das execuções concluíram | `latency_metrics.csv` |
| Throughput | Tarefas concluídas por unidade de tempo | `throughput_metrics.csv` |
| Taxa de erro | Proporção de tarefas com erro/timeout/resposta inválida | `error_metrics.csv` |
| Tempo de recuperação | Da falha induzida até o retorno ao estado estável | `recovery_metrics.csv` |
| Uso de CPU / memória | Consumo por container (média e pico por execução) | `container_stats.csv` |
| Tamanho da fila | Mensagens pendentes no broker ao longo do tempo | `queue_metrics.csv` |
| Tempo de decisão do orquestrador | Estado pronto → ação válida | `decisions.jsonl` |
| Tempo de inferência do LLM | Parcela da chamada ao modelo | `decisions.jsonl` |
| Nº de decisões / chamadas ao LLM | Pontos de decisão e inferências por execução | `decisions.jsonl` |
| Tokens de entrada/saída | Volume de contexto e resposta (quando confiável) | `decisions.jsonl` |
| Overhead decisório absoluto | LLM − Rules no tempo decisório acumulado | `results_summary.csv` |
| Tipo de decisão | `CONTINUE`/`RETRY`/`WAIT`/`FALLBACK`/`ABORT` escolhida | `decisions.jsonl` |
| *Blast radius* | Serviços/tarefas/mensagens afetadas por uma falha | `blast_radius.csv` |

### 10.4.4 Análise (metodologia Tabelas 17–18)

Estatística descritiva (média, mediana, desvio padrão, mín., máx., percentis — P95 para
latência); comparação de médias/medianas; teste *t* quando aplicável; Mann-Whitney para
distribuições não normais; intervalo de confiança; tamanho de efeito; análise estratificada
por cenário; correlação exploratória opcional (tokens/chamadas × tempo de inferência).

A análise quantitativa é complementada por **interpretação qualitativa dos logs de decisão**:
uma resposta LLM pode ser tecnicamente válida mas operacionalmente inadequada. As decisões
são analisadas pelo resultado **e** pelo caminho decisório registrado.

## 10.5 Integridade acadêmica

- **Não** fabricar métricas nem resultados; **não** afirmar que algo foi medido sem
  arquivo/dado correspondente ([`CLAUDE.md`](../CLAUDE.md) §44).
- Diferenciar sempre: **implementado / testado / planejado / provisório**.
- Dados de **piloto** (`phase: PILOT`, `eligible_for_sample: false`) nunca entram na amostra
  definitiva; nunca mover manualmente arquivos de `data/pilot/` para `data/experiment/`.
- Execuções inválidas (`run_status: INVALID`) são registradas em `invalid_runs.csv` e
  **não** substituídas silenciosamente — repete-se a execução com a mesma configuração até
  completar `N_rep` execuções válidas por cenário × abordagem.

## 10.6 Protocolo operacional dos experimentos (resumo — metodologia TABELA 14)

| # | Etapa | Artefato produzido |
|---|---|---|
| 1 | Selecionar cenário, abordagem e repetição; carregar config versionada | `execution_id`, `scenario_id`, `repetition_id` |
| 2 | Restaurar estado inicial (filas/DLQ, dois SQLite, estados transitórios) | `reset.log`, `initial_database_hashes` |
| 3 | Inicializar Docker Compose, serviços, RabbitMQ, orquestrador, coleta | logs de inicialização |
| 4 | Verificar readiness | `readiness_status`, `readiness_timestamp` |
| 5 | (LLM) carregar modelo e executar warm-up | `model_load_ms`, `warmup_inference_ms`, `warmup.log` |
| 6 | Registrar metadados, estado inicial, versões, config | `execution_metadata.json`, `initial_state.json` |
| 7 | Executar carga controlada com dataset e parâmetros do cenário | `task_events.jsonl` |
| 8 | Induzir falha/atraso/inconsistência/sobrecarga conforme config | `fault_events.jsonl` |
| 9 | Coletar estados, decisões, logs, métricas RabbitMQ e de containers | `states.jsonl`, `decisions.jsonl`, `microservices_logs.jsonl`, `queue_metrics.csv`, `container_stats.csv` |
| 10 | Encerrar o cenário pelo critério definido; registrar retorno (ou não) ao *steady state* | `states.jsonl`, `task_events.jsonl`, `execution_metadata.json` |
| 11 | Validar a integridade da execução | `run_validation_status`, `invalid_reason` |
| 12 | Se válida, consolidar métricas; se inválida, registrar motivo e repetir | `metrics_summary.csv`, `invalid_runs.csv` |
| 13 | Após `N_rep` execuções válidas por cenário × abordagem, análise estatística | `analysis_summary.md`, `results.csv` |
