import json
import re
from pathlib import Path

import pytest

from services.orders.app.llm.prompt_builder import PLACEHOLDER, PromptBuilder, serialize_state
from tests.factories import dispatched, make_state, repo_config

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_PATH = REPO_ROOT / repo_config().llm.prompt_template


@pytest.fixture
def builder() -> PromptBuilder:
    return PromptBuilder.from_file(TEMPLATE_PATH)


def test_template_is_the_methodology_prompt(builder):
    # Quadro 2 da metodologia: seções e ações permitidas, na ordem.
    sections = re.findall(r"^[A-ZÇÃÕÁÉÍÓÚ ]+$", builder.template, flags=re.MULTILINE)
    assert sections == [
        "PAPEL",
        "OBJETIVO",
        "AÇÕES PERMITIDAS",
        "RESTRIÇÕES",
        "ESTADO OPERACIONAL",
        "FORMATO OBRIGATÓRIO DA RESPOSTA",
    ]
    assert re.findall(r"^- ([A-Z]+)$", builder.template, flags=re.MULTILINE) == [
        "CONTINUE", "RETRY", "WAIT", "FALLBACK", "ABORT"
    ]
    assert "REDIRECT" not in builder.template and "PARALLELIZE" not in builder.template


def test_prompt_embeds_the_exact_system_state(builder):
    state = dispatched(**{"service.last_result": "timeout"})

    prompt = builder.build(state)

    start = prompt.index("ESTADO OPERACIONAL\n") + len("ESTADO OPERACIONAL\n")
    end = prompt.index("\n\nFORMATO OBRIGATÓRIO")
    assert json.loads(prompt[start:end]) == state.model_dump(mode="json")
    assert PLACEHOLDER not in prompt


def test_prompt_is_rebuilt_only_from_the_current_state(builder):
    first, second = make_state(), dispatched(**{"service.last_result": "timeout"})
    builder.build(first)

    # Sem memória entre chamadas: o prompt do 2º estado é o mesmo com ou sem o 1º antes.
    assert builder.build(second) == PromptBuilder.from_file(TEMPLATE_PATH).build(second)


def test_same_state_gives_the_same_prompt(builder):
    state = make_state()

    assert builder.build(state) == builder.build(state)
    assert serialize_state(state) == serialize_state(state.model_copy())


def test_template_hash_identifies_the_prompt_version(builder):
    assert len(builder.template_hash) == 64
    assert PromptBuilder(builder.template + " ").template_hash != builder.template_hash


@pytest.mark.parametrize("template", ["sem estado", f"{PLACEHOLDER} {PLACEHOLDER}"])
def test_template_must_have_exactly_one_state_placeholder(template):
    with pytest.raises(ValueError):
        PromptBuilder(template)
