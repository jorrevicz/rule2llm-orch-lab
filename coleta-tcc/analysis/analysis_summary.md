# Análise comparativa Rules × LLM

Gerado em 2026-11-15T23:38:47.899Z. Plano `config/analysis_plan.yml`, SHA-256 `ea26e451abb4cb5715d5a0d6b9c4573148bdbe9e4b2383eed76600bfb55ec77c`.
Pares válidos por cenário e repetição; alfa = 0.05, Holm sobre as métricas primárias de cada cenário.
`Δ = LLM − Rules`. `sem_evidencia` **não** significa equivalência.

## inconsistent_data (30 pares)

| Métrica primária | Rules (mediana) | LLM (mediana) | Δ mediana | IC 95% de Δ média | p Holm | Efeito | Conclusão |
|---|---|---|---|---|---|---|---|
| `undue_failure_rate` | 0 | 0 | 0 | [0; 0] | — | — | sem_variacao |
| `latency_median_ms` | 142 | 522.5 | 380 | [379; 383.3] | 3.436e-06 | 1 | favorece_RULES |
| `recovery_restricted_ms` | 149 | 534.5 | 385.5 | [517.7; 1584] | 3.436e-06 | 1 | favorece_RULES |
| `decision_time_total_ms` | 8.85 | 3.068e+04 | 3.068e+04 | [2.973e+04; 3.137e+04] | 5.588e-09 | 1 | favorece_RULES |

Recuperadas: Rules 30/30, LLM 30/30.

## intermittent_failure (30 pares)

| Métrica primária | Rules (mediana) | LLM (mediana) | Δ mediana | IC 95% de Δ média | p Holm | Efeito | Conclusão |
|---|---|---|---|---|---|---|---|
| `undue_failure_rate` | 0 | 0.0333 | 0.0333 | [0.03167; 0.04667] | 5.174e-06 | 1 | sem_evidencia |
| `latency_median_ms` | 142.8 | 527 | 384.2 | [381.9; 386.3] | 5.174e-06 | 1 | favorece_RULES |
| `recovery_restricted_ms` | 147.5 | 535.5 | 386.5 | [250.7; 1321] | 2.159e-05 | 0.8882 | favorece_RULES |
| `decision_time_total_ms` | 10.1 | 3.042e+04 | 3.041e+04 | [2.954e+04; 3.109e+04] | 7.451e-09 | 1 | favorece_RULES |

Recuperadas: Rules 30/30, LLM 30/30.

## normal (30 pares)

| Métrica primária | Rules (mediana) | LLM (mediana) | Δ mediana | IC 95% de Δ média | p Holm | Efeito | Conclusão |
|---|---|---|---|---|---|---|---|
| `undue_failure_rate` | 0 | 0 | 0 | [0; 0] | — | — | sem_variacao |
| `latency_median_ms` | 142 | 522 | 379.8 | [379.2; 382.4] | 1.716e-06 | 1 | favorece_RULES |
| `decision_time_total_ms` | 7.9 | 2.294e+04 | 2.294e+04 | [2.281e+04; 2.301e+04] | 3.725e-09 | 1 | favorece_RULES |

## overload (30 pares)

| Métrica primária | Rules (mediana) | LLM (mediana) | Δ mediana | IC 95% de Δ média | p Holm | Efeito | Conclusão |
|---|---|---|---|---|---|---|---|
| `undue_failure_rate` | 0.1842 | 0 | -0.1842 | [-0.1918; -0.1788] | 5.051e-06 | -1 | favorece_LLM |
| `latency_median_ms` | 2055 | 1.453e+04 | 1.248e+04 | [1.242e+04; 1.254e+04] | 5.051e-06 | 1 | favorece_RULES |
| `recovery_restricted_ms` | 8142 | 5.253e+04 | 4.837e+04 | [4.585e+04; 4.941e+04] | 5.051e-06 | 1 | favorece_RULES |
| `decision_time_total_ms` | 38.55 | 1.998e+06 | 1.998e+06 | [1.988e+06; 2e+06] | 7.451e-09 | 1 | favorece_RULES |

Recuperadas: Rules 30/30, LLM 29/30.

> Aviso: latência condicionada às concluídas: conclusão média Rules=0.815, LLM=1.000

## persistent_degradation (30 pares)

| Métrica primária | Rules (mediana) | LLM (mediana) | Δ mediana | IC 95% de Δ média | p Holm | Efeito | Conclusão |
|---|---|---|---|---|---|---|---|
| `undue_failure_rate` | 0 | 0.2417 | 0.2417 | [0.2355; 0.2572] | 4.764e-06 | 1 | favorece_RULES |
| `latency_median_ms` | 144 | 526.2 | 382 | [379.8; 383.6] | 4.764e-06 | 1 | favorece_RULES |
| `recovery_restricted_ms` | 147.5 | 528 | 381 | [378.1; 784.9] | 4.764e-06 | 1 | favorece_RULES |
| `decision_time_total_ms` | 13.8 | 4.45e+04 | 4.448e+04 | [4.399e+04; 4.524e+04] | 7.451e-09 | 1 | favorece_RULES |

Recuperadas: Rules 30/30, LLM 30/30.

> Aviso: latência condicionada às concluídas: conclusão média Rules=1.000, LLM=0.754

## recovery (30 pares)

| Métrica primária | Rules (mediana) | LLM (mediana) | Δ mediana | IC 95% de Δ média | p Holm | Efeito | Conclusão |
|---|---|---|---|---|---|---|---|
| `undue_failure_rate` | 0.15 | 0.1833 | 0.0333 | [0.0333; 0.0333] | 1.296e-07 | 1 | sem_evidencia |
| `latency_median_ms` | 143 | 526 | 382.5 | [380.7; 384.3] | 3.4e-06 | 1 | favorece_RULES |
| `recovery_restricted_ms` | 1060 | 1218 | 135.5 | [122.1; 610.6] | 1.798e-05 | 0.8968 | favorece_RULES |
| `decision_time_total_ms` | 10.45 | 3.212e+04 | 3.211e+04 | [3.187e+04; 3.217e+04] | 7.451e-09 | 1 | favorece_RULES |

Recuperadas: Rules 30/30, LLM 30/30.

## timeout (30 pares)

| Métrica primária | Rules (mediana) | LLM (mediana) | Δ mediana | IC 95% de Δ média | p Holm | Efeito | Conclusão |
|---|---|---|---|---|---|---|---|
| `undue_failure_rate` | 0 | 0 | 0 | [0.01389; 0.05334] | 0.005005 | 1 | sem_evidencia |
| `latency_median_ms` | 144 | 529 | 384.5 | [380.1; 385.5] | 5.161e-06 | 1 | favorece_RULES |
| `recovery_restricted_ms` | 4147 | 4528 | 382 | [108.5; 916.5] | 5.145e-05 | 0.8796 | sem_evidencia |
| `decision_time_total_ms` | 11.35 | 2.945e+04 | 2.944e+04 | [3.004e+04; 3.366e+04] | 7.451e-09 | 1 | favorece_RULES |

Recuperadas: Rules 30/30, LLM 30/30.

Métricas secundárias são descritivas e estão em `results.csv`.
