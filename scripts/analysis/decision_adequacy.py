"""Rubrica de adequação da decisão (D-33; docs/15 §15.5; metodologia Tabela 17).

Três eixos, iguais para Rules e LLM:

- validade: veredito do Validator comum (já em `decisions.jsonl`);
- oportunidade (ex-ante): a ação proposta responde à condição visível no `SYSTEM_STATE`?
  Usa só o estado e a semântica das ações e admite conjuntos de ações: o Rules não é gabarito;
- resultado (ex-post): o despacho gerado pela decisão teve sucesso, falhou ou expirou?

A rubrica é aplicada depois da execução e nunca é vista pelo decisor.
"""

from collections import defaultdict

from shared.config import ExperimentConfig

ADEQUATE = "ADEQUATE"
INADEQUATE = "INADEQUATE"
UNDETERMINED = "UNDETERMINED"
NO_DECISION = "NO_DECISION"

NOT_DISPATCHED = "NOT_DISPATCHED"
NO_RESPONSE = "NO_RESPONSE"

TRANSIENT_RESULTS = {"timeout", "transient_error"}
NORMAL_FLOW_PHASES = {"PENDING", "WAITING"}
DISPATCHING_ACTIONS = {"CONTINUE", "RETRY", "FALLBACK"}
OUTCOME_EVENTS = {
    "STOCK_RESERVATION_SUCCEEDED": "SUCCEEDED",
    "STOCK_RESERVATION_FAILED": "FAILED",
    "INVENTORY_TIMEOUT": "TIMEOUT",
}
QUALITY_FIELDS = [
    "execution_id", "task_id", "state_id", "decision_id", "decision_engine", "proposed_action", "executed_action",
    "valid", "validation_error", "opportunity_rule", "adequate_actions", "opportunity", "dispatch_result",
    "task_final_status",
]


def adequate_actions(state: dict, config: ExperimentConfig) -> tuple[str, frozenset[str] | None]:
    """Regra aplicada e conjunto de ações adequadas ao estado; `None` = fora da rubrica."""
    task, service, messaging, alternatives = state["task"], state["service"], state["messaging"], state["alternatives"]
    last_result = service.get("last_result")
    pressure = messaging["queue_size"] >= config.messaging.queue_high_watermark
    can_wait = task["wait_count"] < task["max_waits"]
    can_retry = task["attempt_number"] < task["max_attempts"]
    can_fallback = alternatives["fallback_available"] and not alternatives["fallback_used"]

    if task["elapsed_ms"] >= config.messaging.task_deadline_ms:
        return "A1", frozenset({"ABORT"})
    if last_result == "invalid_data":
        return "A2", frozenset({"ABORT"})
    if last_result == "fallback_failed":
        return "A3", frozenset({"ABORT"})
    if service["status"] == "unavailable":
        return "A4", frozenset({"ABORT"} | ({"WAIT"} if can_wait else set()))
    if last_result in TRANSIENT_RESULTS:
        if pressure:
            return "A5", _optional(("WAIT", can_wait), ("FALLBACK", can_fallback), ("ABORT", not can_wait))
        return "A6", _optional(("RETRY", can_retry), ("FALLBACK", can_fallback), ("ABORT", not can_retry and not can_fallback))
    if task["phase"] in NORMAL_FLOW_PHASES:
        if pressure:
            return "A7", _optional(("CONTINUE", True), ("WAIT", can_wait), ("FALLBACK", can_fallback), ("ABORT", not can_wait))
        return "A8", frozenset({"CONTINUE"})
    return "A9", None


def _optional(*options: tuple[str, bool]) -> frozenset[str]:
    return frozenset(action for action, allowed in options if allowed)


def classify_opportunity(state: dict | None, proposed_action: str | None, config: ExperimentConfig) -> tuple[str, frozenset[str] | None, str]:
    """(regra, ações adequadas, classificação)."""
    if state is None:
        return "NO_STATE", None, UNDETERMINED
    rule, actions = adequate_actions(state, config)
    if proposed_action is None:
        return rule, actions, NO_DECISION
    if actions is None:
        return rule, None, UNDETERMINED
    return rule, actions, ADEQUATE if proposed_action in actions else INADEQUATE


def dispatch_result(executed_action: str, sequence: int | None, events: list[dict]) -> str:
    """Efeito do despacho gerado pela decisão: a 1ª resposta ao mesmo destino e tentativa."""
    if executed_action not in DISPATCHING_ACTIONS or sequence is None:
        return NOT_DISPATCHED
    ordered = sorted(events, key=lambda e: e["event_seq"])
    request = next((e for e in ordered if e["event_seq"] > sequence and e["event_type"] == "STOCK_RESERVATION_REQUESTED"), None)
    if request is None:
        return NO_RESPONSE
    for event in ordered:
        if (
            event["event_seq"] > request["event_seq"]
            and event["event_type"] in OUTCOME_EVENTS
            and event.get("target") == request.get("target")
            and event.get("attempt_number") == request.get("attempt_number")
        ):
            return OUTCOME_EVENTS[event["event_type"]]
    return NO_RESPONSE


def evaluate_decisions(
    decisions: list[dict], states: list[dict], events: list[dict], tasks: list[dict], config: ExperimentConfig
) -> tuple[list[dict], dict]:
    """Linhas de `decision_quality.csv` e taxas da execução."""
    by_state = {record["state_id"]: record for record in states}
    by_task: dict[str, list[dict]] = defaultdict(list)
    for event in events:
        by_task[event["task_id"]].append(event)
    final_status = {task["task_id"]: task["status"] for task in tasks}

    rows = []
    for decision in decisions:
        record = by_state.get(decision.get("state_id"))
        state = record.get("system_state", record) if record else None
        proposal = decision.get("proposed_decision")
        proposed_action = proposal.get("action") if proposal else None
        executed_action = decision["executed_decision"]["action"]
        rule, actions, opportunity = classify_opportunity(state, proposed_action, config)
        sequence = record.get("current_event_seq") if record else None
        rows.append(
            {
                "execution_id": decision.get("execution_id"),
                "task_id": decision["task_id"],
                "state_id": decision.get("state_id"),
                "decision_id": decision.get("decision_id"),
                "decision_engine": decision.get("decision_engine"),
                "proposed_action": proposed_action,
                "executed_action": executed_action,
                "valid": decision["validation"]["valid"],
                "validation_error": decision["validation"]["error"],
                "opportunity_rule": rule,
                "adequate_actions": "|".join(sorted(actions)) if actions is not None else None,
                "opportunity": opportunity,
                "dispatch_result": dispatch_result(executed_action, sequence, by_task[decision["task_id"]]),
                "task_final_status": final_status.get(decision["task_id"]),
            }
        )
    return rows, quality_summary(rows)


def quality_summary(rows: list[dict]) -> dict:
    classified = [row for row in rows if row["opportunity"] in {ADEQUATE, INADEQUATE}]
    dispatched = [row for row in rows if row["dispatch_result"] != NOT_DISPATCHED]
    return {
        "decision_valid_rate": round(sum(1 for row in rows if row["valid"]) / len(rows), 4) if rows else None,
        "decision_opportunity_rate": (
            round(sum(1 for row in classified if row["opportunity"] == ADEQUATE) / len(classified), 4) if classified else None
        ),
        "decisions_without_proposal": sum(1 for row in rows if row["opportunity"] == NO_DECISION),
        "decisions_undetermined": sum(1 for row in rows if row["opportunity"] == UNDETERMINED),
        "dispatch_success_rate": (
            round(sum(1 for row in dispatched if row["dispatch_result"] == "SUCCEEDED") / len(dispatched), 4) if dispatched else None
        ),
    }
