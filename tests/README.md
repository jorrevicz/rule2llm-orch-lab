# Testes

```bash
.venv/bin/python -m pytest                    # unitários e de contrato (sem Docker)
.venv/bin/python -m pytest -m integration     # integração: exige o ambiente no ar
```

- `unit/`: lógica isolada, com `orders.db`/`inventory.db` temporários e dublês de broker,
  publicador e agendador (`tests/factories.py`, `Harness`).
- `contracts/`: contratos em `contracts/*.schema.json` em dia com o código e exemplos
  válidos/inválidos (envelope, payloads, `SYSTEM_STATE`, decisão).
- `integration/`: ambiente Docker Compose real, via scripts de piloto
  (`scripts/pilot/*`). Execução de piloto: dados em `data/pilot/`, nunca na amostra.

## Testes mínimos exigidos (CLAUDE §35)

| Exigência | Onde |
|---|---|
| Fluxo normal: pedido → reserva → sucesso | `integration/test_normal_flow.py`; `unit/test_orchestrator.py` |
| Redelivery: a mesma mensagem não duplica reserva | `unit/test_inventory_reservation.py` (`test_redelivery_*`); `integration/test_idempotency.py` |
| `RETRY`: novo `message_id`, mesmo `task_id`, novo `event_seq` | `unit/test_executor_retry.py` (`test_scheduled_attempt_has_new_message_new_seq_same_task_and_target`) |
| Idempotência de negócio | `unit/test_inventory_reservation.py` (`test_new_attempt_*`, `test_task_id_uniqueness_*`) |
| `WAIT` não incrementa `attempt_number` | `unit/test_executor_wait.py` (`test_wait_does_not_consume_an_attempt`) |
| `FALLBACK` só quando admissível | `unit/test_validator.py` (`fallback-*`, `retry-on-fallback`); `unit/test_executor_fallback.py`; `integration/test_fault_scenarios.py` (rota primária degradada → `FALLBACK` executado de fato) |
| `ABORT` produz estado terminal controlado | `unit/test_executor_abort.py`; `integration/test_decision_scenarios.py` |
| Validação de decisões inválidas | `unit/test_validator.py`; `contracts/test_decision.py`; `unit/test_decision_parser.py` (saída do LLM) |
| Decisão inválida → `ABORT` | `unit/test_orchestrator.py` (`test_invalid_proposal_*`, `test_unreadable_output_*`); `unit/test_executor_abort.py`; `unit/test_llm_engine.py` (target inventado, saída malformada, timeout → `LLM_DECISION_TIMEOUT`) |
| Equivalência: Rules e LLM pelo mesmo Validator e Executor | `unit/test_equivalence.py` (inclui o `LLMDecisionEngine` real com cliente falso); `unit/test_validator.py` (`test_every_rules_decision_is_valid`) |

Cenários (M6): catálogo e dataset reprodutíveis (`unit/test_dataset.py`), `invalid_data`, tempo
de serviço e falha injetada no Inventory (`unit/test_inventory_reservation.py`), sorteio e
arquivo de controle (`unit/test_faults.py`), rotas em processos distintos
(`unit/test_inventory_run_workers.py`), seção `fault` e cenários (`unit/test_config.py`,
`unit/test_artifacts.py`), plano de carga (`unit/test_generate_load.py`), janelas de falha
(`unit/test_fault_injectors.py`) e, ao vivo, `integration/test_fault_scenarios.py`.

Protocolo e instrumentação (M7): coleta coerente a 1 s (`unit/test_instrumentation_config.py`),
reset e hash lógico (`unit/test_reset_environment.py`), readiness e cada reprovação
(`unit/test_readiness.py`), exportação do Prometheus e amostrador do Ollama
(`unit/test_metrics_export.py`), ordem alternada, integridade e execuções inválidas
(`unit/test_run_experiment.py`), consolidação das métricas (`unit/test_consolidate.py`) e, ao
vivo, o protocolo completo com todos os artefatos (`integration/test_protocol.py` — reseta o
ambiente).

Também cobertos: LLM stateless e parâmetros do runtime (`unit/test_ollama_client.py`), prompt fixo (`unit/test_prompt_builder.py`), readiness/warm-up (`unit/test_llm_readiness.py`); timeout operacional (`unit/test_timeouts.py`), DLQ e `DEAD_LETTERED`
(`unit/test_dead_letters.py`), numeração e trajetória (`unit/test_orders_event_handler.py`),
`StateBuilder` (`unit/test_state_builder.py`), rastreabilidade
(`unit/test_check_traceability.py`, `integration/test_traceability.py`) e a política
Rules regra a regra (`unit/test_rules_engine.py`).
