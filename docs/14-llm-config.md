# 14 — Configuração do LLM (modelo e runtime)

> Fonte: [`CLAUDE.md`](../CLAUDE.md) §18–20, §40, §43; [03 §3.5](03-stack-tecnologica.md);
> [13-roadmap.md](13-roadmap.md) (D-08, D-09, M5-T08); código em `services/orders/app/llm/`.

Este documento descreve como trocar o **modelo** do `LLMDecisionEngine` (§14.2) e o que
precisa ser implementado para trocar o **runtime** (§14.3).

## 14.1 Antes de trocar qualquer coisa

> **Limitação atual da análise.** `scripts/analysis/consolidate.py` identifica uma execução
> por `scenario_id`, `decision_engine` e `repetition_id`, sem o modelo. Execuções de modelos
> diferentes no mesmo diretório de dados seriam agregadas como um único "LLM". O modelo,
> o digest e a quantização ficam em `execution_metadata.json` de cada execução, mas não
> entram no agrupamento.

## 14.2 Trocar o modelo (mesmo runtime: Ollama)

Nenhum código cita o nome do modelo; ele vem só de `llm.model` na config. Status:
**implementado**.

### Passo 1 — Baixar o modelo no host

O Ollama roda no host, fora do Docker (D-08):

```bash
ollama pull <modelo>:<tag>      # ex.: ollama pull qwen2.5:3b
ollama list                     # confirme o nome exato, incluindo a tag
```

Use **exatamente** o nome mostrado por `ollama list`. A readiness compara o nome literal
com `/api/tags` e `/api/ps` (`llama3.1:8b` ≠ `llama3.1`).

### Passo 2 — Editar `config/experiment_config.yml`

```yaml
llm:
  runtime: ollama
  runtime_version: null         # preenchido a partir do runtime
  model: qwen2.5:3b             # ← único campo obrigatório na troca
  model_digest: null            # lido do runtime pela readiness
  quantization: null            # lida do runtime pela readiness
  temperature: 0.0
  top_p: 1.0
  max_tokens: 64
  seed: 42
  session_memory: false         # obrigatório: false
  request_timeout_seconds: 30
  stream: false                 # obrigatório: false
  response_format: json
  num_ctx: 4096
  keep_alive: 30m
  prompt_template: config/prompts/decision_prompt_v1.txt
```

Não é preciso reconstruir imagens. O diretório `config/` é montado nos containers
(`/srv/config`), e `run_experiment` grava a config efetiva de cada execução.

### Passo 3 — Revisar os parâmetros que dependem do modelo

| Parâmetro | Onde | Quando ajustar |
|---|---|---|
| `max_tokens` (64) | config | Modelos de raciocínio (com saída de "thinking") podem gastar os tokens antes do JSON → `done_reason = length` → saída malformada → `ABORT`. Confirme na bancada (passo 4) que 100% das respostas terminam com `done_reason = stop`. |
| `num_ctx` (4096) | config | O prompt real usa ~550 tokens com o `llama3.1:8b`. Outro tokenizador conta diferente; a bancada verifica `prompt_eval_count < num_ctx`. A readiness reprova se o contexto carregado for menor que `num_ctx`. |
| `request_timeout_seconds` (30) | config | Inferência acima desse valor durante a execução vira `LLM_DECISION_TIMEOUT` → `ABORT` (comportamento da abordagem; a execução continua válida). |
| `seed` | config | Só tem efeito se o modelo e o runtime respeitarem a seed. |
| `PROBE_LIMIT_MS = 7000` | `scripts/pilot/llm_readiness.py` (constante, **fora da config**) | Limite de p95 calculado para o `task_deadline_ms` atual (ver critério 2 do M5). Um modelo mais lento reprova a readiness, e as execuções ficam `INVALID`. Não aumente esse valor só para o modelo passar: ele vem do orçamento de tempo da tarefa. |
| Modelo 100% na GPU | `scripts/pilot/llm_readiness.py` (`_read_placement`) | Um modelo que não cabe inteiro na VRAM com os containers ativos reprova a readiness. |
| Prompt | `config/prompts/decision_prompt_v1.txt` | Vale para qualquer modelo, mas só foi verificado com o `llama3.1:8b`. Qualquer alteração muda o `prompt_template_hash` e exige registro (§14.1). |

### Passo 4 — Rodar a readiness e a bancada de viabilidade

```bash
open -a Ollama                                     # macOS (Linux: ollama serve)
.venv/bin/python -m scripts.pilot.llm_readiness    # PASS/FAIL e motivos, em JSON
docker compose up -d --wait                        # bancada exige os containers ativos
.venv/bin/python -m scripts.pilot.llm_bench --repetitions 5
docker compose down
```

A readiness verifica runtime, modelo presente, digest, quantização, warm-up, 100% na GPU,
contexto e uma inferência de prova ≤ `PROBE_LIMIT_MS`. A bancada (`data/pilot/llm_bench/`)
avalia os quatro critérios de viabilidade do M5: GPU, p95 ≤ 7 s, contexto, `done_reason`.
Ela também relata a validade das decisões, mas isso não é critério.

### Passo 5 — Rodar os testes e uma execução de piloto

```bash
.venv/bin/python -m pytest
.venv/bin/python -m scripts.experiment.run_experiment \
    --scenario normal --engine LLM --repetitions 1
```

Confira em `data/pilot/PILOT_nnnn/execution_metadata.json` os campos `llm.model`,
`llm.model_digest`, `llm.quantization` e `llm_readiness`.

### Passo 6 — Registrar

Registre o modelo, o digest, a quantização, o resultado da bancada e o motivo da troca no
roadmap (decisão D), e atualize 03 §3.2/§3.5 (§14.1).

## 14.3 Trocar o runtime (ex.: llama.cpp server, vLLM, API compatível com OpenAI)

Status: **não implementado**. O runtime é fixo em Ollama: `LLMSection.runtime` é
`Literal["ollama"]`, e vários componentes usam endpoints próprios do Ollama. Os passos
abaixo cobrem todos os pontos de acoplamento encontrados no código.

### Pontos de acoplamento

| Componente | Arquivo | Dependência do Ollama |
|---|---|---|
| Contrato da config | `shared/config.py` (`LLMSection`) | `runtime: Literal["ollama"]`, `response_format: Literal["json"]`, `keep_alive` |
| Cliente de inferência | `services/orders/app/llm/ollama_client.py` | `/api/generate`, `options.num_predict`, `num_ctx`, `format`, `keep_alive`; resposta com `response`, `done_reason`, `prompt_eval_count`, `eval_count`, `total_duration`, `load_duration` (ns) |
| Motor | `services/orders/app/llm/llm_engine.py` | Recebe o tipo concreto `OllamaClient`; usa `Generation` e `LLMTimeout` |
| Montagem | `services/orders/app/orchestration/coordination.py` (`build_engine`) | Sempre instancia `OllamaClient` |
| URL do runtime | `services/orders/app/settings.py`, `docker-compose.yml` | `OLLAMA_BASE_URL` (padrão `host.docker.internal:11434`) |
| Readiness | `scripts/pilot/llm_readiness.py` | `/api/version`, `/api/tags` (digest), `/api/show` (quantização, parâmetros), `/api/ps` (`size_vram`, `context_length`), descarregamento com `keep_alive: 0` |
| Bancada | `scripts/pilot/llm_bench.py` | Usa o mesmo cliente, a mesma readiness e `/api/ps` |
| CPU/RAM do runtime | `scripts/metrics/host_sampler.py` | Procura o processo `ollama serve` e seus descendentes |
| Testes | `tests/unit/test_ollama_client.py`, `test_llm_readiness.py`, `test_llm_engine.py` | Servidores falsos que imitam a API do Ollama |

O que **não** depende do runtime e não deve mudar: `PromptBuilder`, `decision_parser`
(leitura estrita, sem correção), `DecisionValidator`, `DecisionExecutor`, `StateBuilder`,
`DecisionRecord` e a política de decisão inválida → `ABORT` (CLAUDE §22–26).

### Passo a passo

1. **Registrar a decisão antes de implementar.** Abra uma decisão (D) 🔬 no roadmap com o
   motivo, o runtime escolhido e a versão, e o impacto no TCC (§14.1). Trocar o runtime
   muda a condição do tratamento LLM.

2. **Ampliar o contrato da config** (`shared/config.py`):
   - incluir o novo valor em `runtime` (ex.: `Literal["ollama", "llamacpp"]`);
   - revisar os campos específicos do Ollama (`keep_alive`, `response_format`) e decidir
     o equivalente de cada um no novo runtime (ex.: restrição JSON por gramática ou por
     `response_format`);
   - atualizar `tests/unit/test_config.py`.

3. **Definir uma interface do cliente** em `services/orders/app/llm/`:
   - um `Protocol` com `generate(prompt) -> Generation`, e também `version()`, `unload()`
     e as consultas de modelo usadas pela readiness;
   - manter `Generation`, `LLMTimeout` e `LLMRuntimeError` como contrato comum;
   - trocar o tipo do construtor de `LLMDecisionEngine` de `OllamaClient` para o `Protocol`
     (refatoração sem mudança de comportamento).

4. **Implementar o novo cliente** (ex.: `llamacpp_client.py`), preservando as garantias do
   `OllamaClient`:
   - **stateless**: nenhuma sessão, histórico ou cache de conversa entre chamadas (RNF-013);
   - **sem streaming**: uma resposta completa por decisão;
   - **sem ferramentas** (tool calling, funções) nem acesso ao ambiente (RNF-014);
   - mesmos parâmetros de geração (`temperature`, `top_p`, limite de tokens = `max_tokens`,
     `seed`, contexto = `num_ctx`) mapeados para os nomes do runtime;
   - restrição de saída **só à sintaxe JSON**, sem schema (equivalente ao D-09);
   - timeout de `request_timeout_seconds` → `LLMTimeout`; qualquer outra falha →
     `LLMRuntimeError`;
   - preencher `done_reason` e as contagens de tokens a partir da resposta; o que o
     runtime não informar fica `None`, nunca estimado (CLAUDE §44).
   - Use a biblioteca padrão (`urllib`), como o cliente atual. Uma dependência nova segue
     CLAUDE §34.

5. **Escolher o cliente na montagem** (`coordination.py`, `build_engine`): selecionar a
   implementação por `config.llm.runtime`, com a mesma assinatura do motor. Rules,
   Validator e Executor não mudam.

6. **Configurar o endereço do runtime** em `settings.py` e no `docker-compose.yml`: trocar
   ou generalizar `OLLAMA_BASE_URL` (ex.: `LLM_BASE_URL`). O runtime continua fora do
   compose ou passa a rodar dentro dele? Isso é uma decisão de infraestrutura (D-08) e deve
   ser registrada. Não crie um terceiro microsserviço de negócio: o runtime é
   infraestrutura.

7. **Reescrever a readiness** (`scripts/pilot/llm_readiness.py`) com as consultas
   equivalentes do novo runtime:
   - versão do runtime;
   - modelo presente, digest e quantização (se o runtime não informar, registrar `null` e
     o motivo);
   - condição inicial igual em toda execução (descarregar e recarregar o modelo, ou
     equivalente), warm-up fora das métricas e `model_load_ms`;
   - modelo inteiro na GPU e contexto carregado ≥ `num_ctx`;
   - inferência de prova ≤ `PROBE_LIMIT_MS`.
   Se algum item não puder ser verificado no novo runtime, registre isso como limitação da
   bancada. Não remova o critério em silêncio.

8. **Adaptar o amostrador de CPU/RAM** (`scripts/metrics/host_sampler.py`): localizar o
   processo do novo runtime (hoje: `ollama serve`). Sem isso, `container_stats.csv` perde o
   custo de recurso do LLM.

9. **Adaptar a bancada** (`scripts/pilot/llm_bench.py`): ela usa o cliente, a readiness e a
   consulta de modelos carregados. Rode-a e aplique os mesmos quatro critérios do M5.

10. **Testes:** criar testes unitários do novo cliente com um servidor falso. Cubra corpo da
    requisição, ausência de estado entre chamadas, timeout → `LLMTimeout`, erro HTTP →
    `LLMRuntimeError` e campos ausentes → `None`. Repita para a readiness. Mantenha os
    testes de equivalência (Rules e LLM passam pelo mesmo Validator e Executor) e rode
    `.venv/bin/python -m pytest`.

11. **Rastreabilidade:** confirmar que `execution_metadata.json` registra `llm.runtime`,
    `llm_runtime_version`, modelo, digest, quantização e `prompt_template_hash`. Se mais de
    um runtime ou modelo coexistir nos dados, incluir esses campos na identificação da
    execução em `scripts/analysis/consolidate.py` (ver a limitação em §14.1).

12. **Piloto e documentação:** rodar execuções de piloto com o novo runtime (nunca em
    `data/experiment/`), atualizar 03 §3.2/§3.5, este documento, `piloto-do-experimento.md`
    e o capítulo metodológico, e só depois congelar.
