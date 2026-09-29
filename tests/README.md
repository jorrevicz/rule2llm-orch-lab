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
| `FALLBACK` só quando admissível | `unit/test_validator.py` (`fallback-*`, `retry-on-fallback`); `unit/test_executor_fallback.py` |
| `ABORT` produz estado terminal controlado | `unit/test_executor_abort.py`; `integration/test_decision_scenarios.py` |
| Validação de decisões inválidas | `unit/test_validator.py`; `contracts/test_decision.py`; `unit/test_decision_parser.py` (saída do LLM) |
| Decisão inválida → `ABORT` | `unit/test_orchestrator.py` (`test_invalid_proposal_*`, `test_unreadable_output_*`); `unit/test_executor_abort.py`; `unit/test_llm_engine.py` (target inventado, saída malformada, timeout → `LLM_DECISION_TIMEOUT`) |
| Equivalência: Rules e LLM pelo mesmo Validator e Executor | `unit/test_equivalence.py` (inclui o `LLMDecisionEngine` real com cliente falso); `unit/test_validator.py` (`test_every_rules_decision_is_valid`) |

Também cobertos: LLM stateless e parâmetros do runtime (`unit/test_ollama_client.py`), prompt fixo (`unit/test_prompt_builder.py`), readiness/warm-up (`unit/test_llm_readiness.py`); timeout operacional (`unit/test_timeouts.py`), DLQ e `DEAD_LETTERED`
(`unit/test_dead_letters.py`), numeração e trajetória (`unit/test_orders_event_handler.py`),
`StateBuilder` (`unit/test_state_builder.py`), rastreabilidade
(`unit/test_check_traceability.py`, `integration/test_traceability.py`) e a política
Rules regra a regra (`unit/test_rules_engine.py`).
