# Documentação — `rule2llm-orch-lab`

Orquestração de tarefas assíncronas em microsserviços: **`RulesDecisionEngine` versus agente LLM**.

Este diretório consolida, em formato navegável, as definições dispersas na metodologia do
TCC e no documento de piloto técnico. É também um artefato de reprodutibilidade científica:
a documentação deve permanecer coerente com a metodologia e com o código.

## Público-alvo

- Quem vai **implementar** o piloto técnico e a bancada experimental.
- Quem vai **revisar** o alinhamento entre código, piloto e capítulo metodológico.
- A **banca / leitura acadêmica**, para entender arquitetura, contratos e coleta.

## Como navegar

| # | Documento | Conteúdo |
|---|-----------|----------|
| 01 | [Visão geral](01-visao-geral.md) | Contexto acadêmico, objetivo, variável experimental, escopo e não-escopo |
| 02 | [Arquitetura](02-arquitetura.md) | Dois microsserviços, camada de coordenação, diagrama de arquitetura, isolamento |
| 03 | [Stack tecnológica](03-stack-tecnologica.md) | Papel de cada tecnologia, o que **não** faz, status de congelamento |
| 04 | [Contrato de mensageria](04-contrato-mensageria.md) | Envelope versionado, filas/targets, redelivery × RETRY, idempotência, **diagrama de atividades da mensageria** |
| 05 | [Máquina de estados](05-maquina-de-estados.md) | Estados da tarefa e do pedido, mapeamento, diagrama de estados |
| 06 | [Modelo de decisão](06-modelo-de-decisao.md) | `SYSTEM_STATE`, espaço de ações, contrato de decisão, Rules, Validator, Executor, LLM, decisão inválida |
| 07 | [Diagramas UML](07-diagramas-uml.md) | **Casos de uso**, **classes**, **sequência** (6 cenários), atividades da mensageria |
| 08 | [Modelo de dados (MER)](08-modelo-de-dados-mer.md) | MER de `orders.db` e de `inventory.db` |
| 09 | [Dicionário de dados](09-dicionario-de-dados.md) | Tabelas e colunas de `orders.db` e `inventory.db` |
| 10 | [Rastreabilidade e métricas](10-rastreabilidade-e-metricas.md) | Artefatos de coleta, correlação de identificadores, métricas |
| 11 | [Requisitos](11-requisitos.md) | RF-001..RF-042 e RNF-001..RNF-030 |
| 12 | [Glossário](12-glossario.md) | Termos, identificadores, `reason_code`, ações, targets |

## Relação com `docs/ref/`

`docs/ref/` contém o material-fonte (não versionado — ver `.gitignore`):

- `piloto-do-experimento.md` — decisões de implementação do piloto (37 seções).
- `TCC_METODOLOGIA.pdf` — capítulo 4 da metodologia (seções 4.1–4.6).

Quando este diretório e o material-fonte divergirem, a ordem de precedência é a do
[`CLAUDE.md`](../CLAUDE.md) §46: instrução atual do responsável → decisões congeladas →
`piloto-do-experimento.md` → `CLAUDE.md` → convenções → engenharia genérica.

## Status

Documentação do **piloto técnico**. Parâmetros, versões de software e o modelo LLM ainda
estão sujeitos a congelamento antes da coleta definitiva (ver
[11-requisitos.md](11-requisitos.md) e [03-stack-tecnologica.md](03-stack-tecnologica.md)).

## Convenções

- **Implementado / Testado / Planejado / Provisório** são marcados explicitamente.
- Diagramas em [Mermaid](https://mermaid.js.org/) (renderizam no VS Code e no GitHub).
- Timestamps em UTC, ISO 8601 (`2026-08-27T12:00:00.000Z`).
- Identificadores com prefixo: `EXP_`, `PILOT_`, `ORD_`, `TASK_`, `MSG_`, `STATE_`, `DEC_`.
