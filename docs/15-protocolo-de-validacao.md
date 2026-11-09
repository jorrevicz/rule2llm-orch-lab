# 15 — Protocolo de validação dos resultados

> Fonte: orientação acadêmica sobre a fundamentação dos resultados e auditoria do delineamento
> (2026-11-08); metodologia §4.4–§4.6; decisões D-24 a D-36 ([13 §13.4](13-roadmap.md)).
> Status: **implementado e provisório** até a tag `freeze-v1`. Nenhum número deste documento é
> resultado da amostra.

## 15.1 Finalidade

Os resultados do experimento são evidência original. O que precisa de fundamentação é o que os
produz e os interpreta: métricas, medição, análise e critérios. Este protocolo fixa, **antes da
coleta definitiva**, quatro exigências:

1. cada métrica tem definição operacional, referência e cálculo verificável (§15.3);
2. cada cenário tem justificativa e critério objetivo de avaliação (§15.4);
3. cada resultado é rastreável aos dados brutos e ao procedimento de análise (§15.8);
4. cada conclusão indica as evidências, os estudos comparáveis e as limitações (§15.9).

A validação tem três níveis: **construto** (o que a variável representa), **instrumental** (os
instrumentos medem o pretendido, em condições equivalentes) e **conclusão** (estatística
justificada, incerteza declarada, explicações alternativas examinadas).

## 15.2 Hipóteses operacionais

O capítulo 4 menciona uma "hipótese principal" que não está no material deste repositório (o PDF
contém só a metodologia). As formulações abaixo operacionalizam o objetivo geral e **precisam ser
conciliadas com o texto da Introdução**.

Para cada cenário `s` e métrica primária `Y`, com pares `(L, R)` da mesma repetição:
`H0: a distribuição de Y(L, s) − Y(R, s) é simétrica em torno de zero`, contra alternativa bicaudal.

| Hipótese | Pergunta | Métrica primária | Evidência complementar |
|---|---|---|---|
| H-CUSTO | O LLM altera o custo de decidir? | `decision_time_total_ms` | `llm_compute_total_ms`, `llm_queue_wait_total_ms`, tokens, CPU/RAM |
| H-DESEMPENHO | O mecanismo altera a latência ponta a ponta? | `latency_median_ms` | P95, `completion_rate` (obrigatória, ver §15.3) |
| H-CONFIABILIDADE | O mecanismo altera as falhas indevidas? | `undue_failure_rate` | `error_rate`, `failed_with_reservation` |
| H-RESILIÊNCIA | O mecanismo altera o retorno ao estado estável? | `recovery_restricted_ms` | `recovery_rate`, *blast radius* |
| H-ADAPTABILIDADE | O LLM decide diferente, e melhor, diante do mesmo estado? | Sem teste confirmatório na execução | Reavaliação offline (§15.6) e `persistent_degradation` (§15.4) |

Regra de leitura: rejeitar `H0` num cenário sustenta uma afirmação **sobre aquele cenário e aquela
configuração**. Não rejeitar não demonstra equivalência.

## 15.3 Fichas das métricas

Status: **M** mensurável e fundamentada na metodologia; **C** referência apenas conceitual, sem
comparabilidade direta; **D** definição operacional própria deste trabalho.

Os indicadores são calculados por `scripts/analysis/consolidate.py`, salvo indicação, e aparecem
em `metrics_summary.csv`.

| Métrica | Definição operacional | Unidade | Origem | Referência | Status | Cuidado de interpretação |
|---|---|---|---|---|:---:|---|
| `decision_time_total_ms` | Soma de `decision_time_ms` (estado pronto → ação válida) | ms | `decisions.jsonl` | Metodologia eq. 4.1–4.2 | M | Inclui a fila do runtime no LLM |
| `llm_inference_total_ms` | Soma do tempo da chamada ao runtime | ms | `decisions.jsonl` | Metodologia Tabela 15 | M | **Inclui fila** (D-29) |
| `llm_compute_total_ms` | Soma de `prompt_eval_duration + eval_duration` | ms | `decisions.jsonl` | D-29 | D | Nulo em timeout: não há resposta do runtime |
| `llm_queue_wait_*` | Chamada − processamento; total, mediana e P95 | ms | `decisions.jsonl` | D-29 | D | Também contém o transporte HTTP (poucos ms). Só vê a espera dentro do Ollama; com `orders-worker` de concorrência 1, fica limitada a cerca de uma inferência e não mede a fila em `orders.events` |
| `llm_fault_window_utilization` | Cômputo do LLM dentro da janela de falha / duração da janela, com recorte nas bordas | razão | `decisions.jsonl`, `fault_events.jsonl` | D-37 | D | Nula no Rules e sem janela de falha. Critério de calibração, não desfecho |
| Tokens e chamadas | Soma e razão por tarefa | contagem | `decisions.jsonl` | Cornacchia et al. (2025), como indicador de custo | C | Não equivale a acurácia diagnóstica |
| `latency_*_ms` | `TASK_CREATED` → `TASK_COMPLETED`, só tarefas concluídas | ms | `task_events.jsonl` | Metodologia eq. e Tabela 17 | M | **Condicionada a sobreviventes**: ler com `completion_rate` |
| `completion_rate` | Tarefas `COMPLETED` / tarefas criadas | razão | `task_events.jsonl` | D-32 | D | — |
| `undue_failure_rate` | Pedidos válidos que não concluíram / pedidos válidos | razão | `task_events.jsonl`, `fault_events.jsonl` | D-32 | D | Não penaliza a rejeição correta de dado inválido |
| `error_rate` | Tarefas não concluídas / tarefas | razão | `task_events.jsonl` | Metodologia Tabela 15 | M | Conta `ABORT` correto como erro; métrica secundária |
| `failed_with_reservation` | Tarefas `ABORTED`/`DEAD_LETTERED` com reserva efetivada | contagem | `task_events.jsonl`, logs do Inventory | D-32 | D | Inconsistência final; não há ação de compensação no recorte |
| `throughput_per_s` | Concluídas / (último término − primeira criação) | 1/s | `task_events.jsonl` | Metodologia Tabela 17 | M | Em malha aberta sem saturação, reflete a taxa de chegada |
| `recovery_after_fault_end_ms` | 1º pedido da janela de `k` sucessos normais − fim da falha | ms | `latency_metrics.csv` | D-24, D-28; Zhang et al. (2024) para a injeção | D | Referência é o próprio pré-falha da execução |
| `recovery_time_ms` | Mesmo instante − início da falha | ms | `latency_metrics.csv` | Metodologia Tabela 17 | M | Tem piso igual à duração da falha |
| `recovery_rate` | Execuções recuperadas / calculáveis | razão | `compare_engines.py` | D-28 | D | Sempre ao lado do tempo |
| `recovery_restricted_ms` | Tempo pós-falha limitado a um horizonte `τ` comum; não recuperado = `τ` | ms | `compare_engines.py` | D-28; média restrita | D | `τ` é o menor horizonte observado no cenário |
| Blast radius | Tarefas com mais de um ponto de decisão ou não concluídas | contagem | `blast_radius.csv` | D-25 | D | Limitado por dois serviços |
| CPU/RAM | Média, pico e núcleo-segundos por componente | %, bytes | `container_stats.csv` | Metodologia Tabela 15 | M | **GPU não medida** (D-36): custo do LLM subestimado |
| Pico de fila | Máximo de mensagens por fila | contagem | `queue_metrics.csv` | Metodologia Tabela 15 | M | Amostragem de 1 s |
| Validade | Decisões aprovadas pelo Validator | razão | `decisions.jsonl` | Metodologia Tabela 17 | M | — |
| Oportunidade e resultado | Rubrica de §15.5 | razão | `decision_quality.csv` | D-33 | D | Critério próprio, publicado antes da coleta |

**Validação instrumental.** Testes unitários conferem cada cálculo com dados de resultado conhecido
(`tests/unit/test_consolidate.py`, `test_decision_adequacy.py`, `test_compare_engines.py`). O relógio é
único por host, os tempos de decisão usam relógio monotônico e a integridade é verificada a cada
execução (Tabela 14, etapa 11).

**Pendente:** confirmar por leitura do artigo primário cada referência marcada C. Os três trabalhos
abaixo **não são benchmarks reproduzidos** aqui:

- Zhang et al. (2024) fundamenta a injeção controlada de falhas, não a definição de recuperação;
- Cornacchia et al. (2025) justifica investigar custo e comportamento decisório, sem equiparar
  acurácia diagnóstica à taxa de conclusão de tarefas;
- Yu, Ding e Sato (2025) fundamenta a relevância da eficiência operacional; a paralelização
  adaptativa está fora deste recorte.

## 15.4 Cenários

Parâmetros em `config/scenarios/`, provisórios até M8-T02. **Taxa e janelas seguem D-30.** A seed de
falha varia por repetição e é igual dentro do par Rules/LLM (D-27).

| Cenário | Justificativa | Sinal visível ao decisor | Pode distinguir decisão contextual? | Critério de avaliação |
|---|---|---|---|---|
| `normal` | Linha de base, steady state | Nenhum | Não: mede custo | Custo decisório; latência |
| `overload` | Único teste de saturação (D-30) | `queue_size`, `latency_ms` | Pouco: domina a capacidade do decisor | Conclusão, recuperação, filas |
| `intermittent_failure` | Falha transitória, retentativa com chance de sucesso | `last_result = transient_error` | Parcial: `RETRY` e `FALLBACK` são razoáveis | Falha indevida, recuperação |
| `persistent_degradation` | Falha quase certa na primária com o fallback saudável (D-31) | `transient_error`, `fallback_available` | **Sim**: `FALLBACK` antecipado evita tentativas perdidas | Latência das tarefas afetadas, resultado do despacho |
| `timeout` | Resposta tardia acima do timeout operacional | `last_result = timeout` | Parcial | Falha indevida, efeito de reentrega |
| `inconsistent_data` | Dado que nenhuma ação corrige | `last_result = invalid_data` | Sim, quanto a gastar ações inúteis | Desfecho correto, decisões por tarefa |
| `recovery` | Indisponibilidade total do serviço | `service.status = unavailable` | Sim, entre `WAIT` e `ABORT` | Recuperação, conclusão |

**Calibração da carga (D-30, revista por D-37).** Com um runtime serial, uma taxa que satura o decisor
mede a capacidade do runtime, não a decisão. Rules e LLM recebem a mesma carga; a conclusão vale para
esse ponto de operação.

**Critério de não saturação (D-37).** Nas execuções com LLM fora de `overload`,
`llm_fault_window_utilization ≤ 0,70`: o tempo de cômputo do LLM dentro da janela de falha, dividido
pela duração da janela. O limite está em `config/analysis_plan.yml`, e `compare_engines` avisa no
resumo quando uma execução o ultrapassa. Reprovar leva a recalibrar **antes** do congelamento. Durante
a coleta, lentidão do LLM **nunca** invalida execução (CLAUDE §24, §31).

O limite de 0,70 tem duas bases, ambas provisórias:

- **Piloto M8-T17 (4 execuções com LLM):** com 0,32 e 0,46 não houve atraso além do observado no
  Rules; com 0,83, atraso de uma inferência; com 0,98, fila acumulada (p95 de 18,22 s no despacho do
  `RETRY`) e 4 tarefas abortadas por prazo, por efeito de capacidade.
- **Orçamento do prazo da tarefa:** 4 decisões a 7 s gastam 28 s dos 60 s de `task_deadline_ms`,
  sobrando cerca de 8 s de espera por decisão. Na aproximação M/D/1, `espera média = ρ / (2(1 − ρ)) × S`,
  8 s de espera com `S` = 7 s corresponde a ρ ≈ 0,70. A aproximação usa a média, não a cauda.

O critério anterior (`llm_queue_wait_p95_ms ≤ llm_compute_median_ms`) foi abandonado porque só vê a
espera dentro do Ollama. Com `orders-worker` de concorrência 1, essa espera fica limitada a cerca de
uma inferência, e a fila real se forma em `orders.events`.

**Carga provisória.** A pior utilização observada a 0,05/s foi 0,98, o que dá
`0,05 × 0,70 / 0,98 ≈ 0,036/s`; adotado **0,035/s**. Para manter 10 tarefas antes, 10 durante e 10 depois
da falha, a falha começa aos 280 s e dura 280 s (`recovery`: 140 s, cerca de 5 tarefas). Em `overload`,
a rajada de 10 s começa aos 285,7 s, junto do 11º envio; se começasse antes dele, o gerador perderia
parte da janela e enviaria 65 pedidos em vez de 150. A taxa final sai de M8-T02, medida no servidor: o que se transfere entre
máquinas é o limite, não a taxa.

**Verificação (M8-T21, dado de piloto).** A 0,035/s, em `persistent_degradation`, a utilização foi de 0,48
e 0,60 e os atrasos no Orders ficaram iguais aos do Rules. A inferência estava mais rápida que no piloto
anterior (mediana de 3,7–3,9 s, contra 4,9–5,3 s); naquela velocidade, a utilização seria de cerca de 0,8.
Por isso o critério precisa ser reavaliado no servidor final.

**Alinhamento a fixar em M8-T02.** O início e o fim da janela devem coincidir com envios da carga. Com
fim aos 560 s e envio aos 571,4 s, o tempo de recuperação ganha um piso de 11,4 s, igual nos dois motores,
que infla o valor absoluto sem deslocar a diferença pareada.

## 15.5 Rubrica de adequação da decisão (D-33)

Implementada em `scripts/analysis/decision_adequacy.py`; aplicada igualmente aos dois motores e
**gravada antes da coleta**. Tabela 17 da metodologia: validade, oportunidade e resultado.

- **Validade:** veredito do Validator comum.
- **Oportunidade (ex-ante):** a ação proposta responde à condição visível no `SYSTEM_STATE`? Usa só
  o estado e a semântica das ações ([06](06-modelo-de-decisao.md)), e admite **conjuntos** de ações.
  Proposta ausente (saída ilegível ou timeout) → `NO_DECISION`.
- **Resultado (ex-post):** o despacho gerado pela decisão teve sucesso, falhou ou expirou?

Tabela de oportunidade, na ordem de avaliação (`pressão` = `queue_size ≥ queue_high_watermark`;
`espera disponível` = `wait_count < max_waits`; `tentativas` = `attempt_number < max_attempts`;
`fallback` = disponível e não usado):

| Regra | Condição | Ações adequadas | Razão |
|---|---|---|---|
| A1 | `elapsed_ms ≥ task_deadline_ms` | `ABORT` | Prazo esgotado |
| A2 | `last_result = invalid_data` | `ABORT` | O dado não muda com rota nem tempo |
| A3 | `last_result = fallback_failed` | `ABORT` | Não resta rota |
| A4 | `service.status = unavailable` | `WAIT` se espera disponível; `ABORT` | Fallback pertence ao mesmo serviço (CLAUDE §15) |
| A5 | Falha transitória ou timeout, com pressão | `WAIT` se espera disponível; `FALLBACK` se fallback; `ABORT` sem espera | `RETRY` realimenta a fila |
| A6 | Falha transitória ou timeout, sem pressão | `RETRY` se tentativas; `FALLBACK` se fallback; `ABORT` se nenhum dos dois | Desistir com alternativa é prematuro |
| A7 | Sem falha pendente, com pressão | `CONTINUE`, `WAIT` se espera disponível; `FALLBACK` se fallback; `ABORT` sem espera | Fila não impede despacho |
| A8 | Sem falha pendente, sem pressão | `CONTINUE` | Fluxo normal |
| A9 | Demais estados | `UNDETERMINED` | Fora da rubrica; analisar caso a caso |

**Limite declarado.** A rubrica não usa o Rules como gabarito: reconhece, por exemplo, `FALLBACK`
antecipado como adequado, algo que o Rules nunca faz. Mas codifica a mesma semântica de ações, e por isso
o Rules tende a 100% nesse eixo **por construção**. Essa taxa serve para saber se o LLM responde ao
estado; não mede superioridade. Vantagem contextual só pode aparecer no eixo de **resultado** e nas
métricas sistêmicas.

## 15.6 Reavaliação offline dos mesmos estados (D-34)

`scripts/analysis/replay_decisions.py` responde à Tabela 17 ("diante de estados equivalentes") sem o
efeito de filas e timers. Toma os `SYSTEM_STATE` gravados nas execuções válidas, sorteia por estrato de
forma determinística e submete cada estado, sequencialmente, aos dois motores e ao Validator comum.

Saída: `data/<fase>/replay/<id>/replay_decisions.jsonl` e `replay_summary.csv` (concordância, validade e
oportunidade por estrato). Não executa ações e não integra a amostra de execuções: é análise do
mecanismo decisório isolado. O desempenho sistêmico continua vindo das execuções.

## 15.7 Plano de análise (D-35)

Fixado em `config/analysis_plan.yml`, cujo hash vai para os resultados; executado por
`scripts/analysis/compare_engines.py`.

| Item | Definição |
|---|---|
| Unidade | Uma execução válida, pareada por cenário e `repetition_id` (mesma seed de falha) |
| Estimando | `Δ = L − R`: média e mediana das diferenças pareadas |
| Incerteza | IC 95% da média por bootstrap pareado percentil (seed fixa) |
| Teste | Wilcoxon signed-rank bicaudal, zeros descartados |
| Efeito | Correlação rank-biserial pareada e `Δ` absoluto |
| Multiplicidade | Holm sobre as métricas primárias **de cada cenário**; secundárias são descritivas |
| Relevância | Tempo: `|Δ mediana| ≥ 10%` da mediana do Rules; razão: `≥ 0,05` |
| Sem variação | Todas as diferenças nulas → sem teste, rótulo `sem_variacao` |

Rótulos: `favorece_LLM`, `favorece_RULES` (p ajustado < 0,05, IC exclui zero e diferença relevante),
`inconclusivo` (diferença relevante sem significância) e `sem_evidencia` (o restante; **não** é equivalência).

Mann-Whitney e teste *t*, previstos na Tabela 18, deixam de ser os testes principais: as observações
são pareadas, e `N_rep` será pequeno demais para verificar normalidade com poder razoável.

**Piso de `N_rep`.** Em Wilcoxon exato, o menor p bicaudal é `2 / 2^n`. São necessários ao menos 6 pares
sem correção e **8 pares** com Holm sobre 4 métricas primárias. O valor final é decidido em M8-T02.

**Repetições sem sorteio.** `normal`, `overload` e `recovery` não têm seed de falha: nas repetições
variam apenas o ruído da bancada e as respostas do LLM. Para eles, o intervalo descreve a repetibilidade
daquela configuração, e não variação de padrões de falha.

## 15.8 Rastreabilidade do resultado

Cada linha de `results.csv` traz os `execution_id` usados, o hash do plano de análise e os commits. Cada
execução guarda config efetiva, seeds e hashes. O caminho é:

```
afirmação no TCC → linha de results.csv → execution_ids → metrics_summary.csv
                 → latency_metrics.csv / decision_quality.csv → task_events, decisions, states
```

Só entram na amostra execuções `VALID` com `eligible_for_sample = true`. Rodar a análise sobre piloto
exige `--phase pilot` e o relatório sai com o aviso **PILOTO — não é resultado da amostra**.

## 15.9 Matriz de conclusões (a preencher após a coleta)

Nenhuma conclusão entra no TCC sem uma linha completa.

| Afirmação | Cenário e configuração | Evidência (`results.csv`) | Incerteza e efeito | Explicações alternativas examinadas | Estudos legitimamente comparáveis | Limitações |
|---|---|---|---|---|---|---|
| | | | | | | |

Explicações alternativas a examinar sempre: fila do runtime (`llm_queue_wait_*`), fila em `orders.events` (`llm_fault_window_utilization`), ordem de execução,
efeito de sobreviventes, calibração da carga, e se a informação necessária estava no `SYSTEM_STATE`.

Comparação com a literatura exige demonstrar métrica, tarefa e condições equivalentes. Sem isso, a
discussão é apenas de contexto.

## 15.10 Capacidade demonstrativa

| Categoria | Itens |
|---|---|
| Fundamentado e mensurável | Tempo de decisão, tokens, chamadas, validade; latência, erro, CPU/RAM, fila; equivalência de Validator, Executor e estado |
| Fundamentação conceitual, sem comparabilidade direta | Injeção de falhas; custo decisório; eficiência operacional |
| Definição própria, a validar no ensaio | Recuperação, falha indevida, rubrica, calibração da carga |
| Ultrapassa o experimento | Generalizar para produção ou para outros modelos; superioridade geral de LLM; energia; custo de GPU; adaptabilidade fora dos cenários e do espaço de ações |

## 15.11 Referências metodológicas

Bibliografia conferida em 2026-11-08; **ler antes de citar** e incluir no TCC somente o que for usado.

- ARCURI, A.; BRIAND, L. A practical guide for using statistical tests to assess randomized algorithms in software engineering. ICSE 2011. DOI 10.1145/1985793.1985795.
- BASIRI, A. et al. Chaos Engineering. IEEE Software, v. 33, n. 3, p. 35–41, 2016. DOI 10.1109/MS.2016.60.
- HOLM, S. A simple sequentially rejective multiple test procedure. Scandinavian Journal of Statistics, v. 6, n. 2, p. 65–70, 1979.
- KERBY, D. S. The simple difference formula: an approach to teaching nonparametric correlation. Comprehensive Psychology, v. 3, 2014.
- PAPADOPOULOS, A. V. et al. Methodological principles for reproducible performance evaluation in cloud computing. IEEE TSE, v. 47, n. 8, p. 1528–1543, 2021. DOI 10.1109/TSE.2019.2927908.
- ROYSTON, P.; PARMAR, M. K. B. Restricted mean survival time: an alternative to the hazard ratio for the design and analysis of randomized trials with a time-to-event outcome. BMC Medical Research Methodology, v. 13, art. 152, 2013. DOI 10.1186/1471-2288-13-152.
- WILCOXON, F. Individual comparisons by ranking methods. Biometrics Bulletin, v. 1, n. 6, p. 80–83, 1945. DOI 10.2307/3001968.

## 15.12 Verificação antes do `freeze-v1`

- [ ] Hipóteses conciliadas com a Introdução do TCC.
- [ ] Artigos primários e fontes do Consensus lidos; status C de §15.3 confirmado ou corrigido.
- [ ] Critério de não saturação aprovado no servidor final.
- [ ] `N_rep` definido com o piso de §15.7.
- [ ] `config/analysis_plan.yml` e a rubrica incluídos no manifesto de hashes (M8-T04).
