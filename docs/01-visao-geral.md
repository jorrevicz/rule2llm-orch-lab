# 01 — Visão geral

> Fonte: Com base no delineamento do experimento definido pela Pesquisa formulada no trabalho acadêmico de TCC. 

## 1.1 O que é este projeto

Ambiente experimental controlado que simula, em escala reduzida, uma arquitetura de
microsserviços com coordenação de tarefas assíncronas. O experimento compara **dois
mecanismos de decisão** para escolher a próxima ação de orquestração diante de um estado
operacional:

1. **`RulesDecisionEngine`** — abordagem tradicional: determinística, explícita, auditável,
   baseada em regras previamente definidas e congeladas.
2. **`LLMDecisionEngine`** — abordagem baseada em um modelo de linguagem executado
   localmente (Ollama), que seleciona a ação a partir de um prompt fixo + estado.

O caso de negócio é uma jornada fictícia e simplificada de processamento de pedidos:
**Pedido → Reserva de estoque → Conclusão**.

## 1.2 Caracterização da pesquisa

| Dimensão | Classificação |
|---|---|
| Abordagem | Quali-quantitativa, com predominância quantitativa |
| Objetivos | Exploratória, descritiva e explicativa |
| Procedimento | Experimental (objeto de estudo, variáveis controladas, observação de efeitos) |
| Sujeitos | Artefatos computacionais (não há sujeitos humanos) |
| Unidade de análise | Cada execução experimental sob um cenário específico |

O uso de IA generativa como ferramenta de apoio ao desenvolvimento é declarado na
metodologia (§4.2), sem substituição da autoria nem da responsabilidade intelectual.

## 1.3 Objetivos

**Geral.** Avaliar experimentalmente o uso de um agente baseado em LLM na tomada de decisão
para orquestração dinâmica de tarefas assíncronas em uma arquitetura simulada de
microsserviços.

**Específicos** (metodologia, TABELA 19):

| ID | Objetivo específico |
|---|---|
| OE1 | Implementar uma arquitetura simulada de microsserviços com comunicação assíncrona e containerização |
| OE2 | Desenvolver e integrar um agente LLM que apoie decisões de orquestração em cenários pré-definidos |
| OE3 | Definir métricas de desempenho, resiliência e custo decisório para comparar as duas abordagens |
| OE4 | Executar experimentos controlados em diferentes cenários (normal, sobrecarga, falha intermitente, timeout, dados inconsistentes, recuperação pós-falha) |
| OE5 | Analisar e discutir os resultados, destacando vantagens, limitações e aplicações futuras |

## 1.4 A variável experimental

A comparação deve preservar **uma única diferença principal**:

```
RulesDecisionEngine   versus   LLMDecisionEngine
```

Tudo o mais é mantido equivalente entre as duas condições:

- mesma arquitetura, microsserviços, bancos, RabbitMQ, Celery;
- mesmo `StateBuilder` e mesmo contrato de `SYSTEM_STATE`;
- mesmo conjunto de ações (`CONTINUE`, `RETRY`, `WAIT`, `FALLBACK`, `ABORT`);
- mesmo `DecisionValidator` e mesmo `DecisionExecutor`;
- mesmos limites operacionais (timeout, `max_attempts`, `max_waits`, etc.);
- mesma instrumentação, dataset, política de reset, política de readiness;
- mesmos cenários experimentais e mesma coleta de métricas.

A seleção do mecanismo é interna e controlada por configuração:

```yaml
decision_engine: rules   # ou: llm
```

Ver [02-arquitetura.md §2.4](02-arquitetura.md) (princípio de equivalência) e
[11-requisitos.md](11-requisitos.md) (RNF-001 a RNF-013, RNF-019, RNF-026 a RNF-029).

## 1.5 Escopo — o que **está** no recorte

- Dois microsserviços de negócio: `orders-service` e `inventory-service`.
- Comunicação assíncrona por RabbitMQ; execução assíncrona por Celery.
- Persistência SQLite independente por serviço.
- Fluxo Pedido → Estoque de ponta a ponta.
- Idempotência de transporte (`message_id`) e de negócio (`task_id`).
- `StateBuilder` → `SYSTEM_STATE` → `DecisionEngine` → `DecisionValidator` → `DecisionExecutor`.
- Cinco ações executáveis.
- Política de decisão inválida → registrar → `ABORT`.
- `LLMDecisionEngine` stateless via Ollama, com prompt fixo.
- Rastreabilidade em arquivos JSONL/CSV.
- Seis cenários experimentais (normal, sobrecarga, falha intermitente, timeout, dados
  inconsistentes, recuperação pós-falha).
- Métricas de desempenho, resiliência e custo decisório.

## 1.6 Não-escopo — o que **não** está no recorte

> Fonte: metodologia §4.6; `piloto-do-experimento.md` §30 (RNF-030), §33–35;
> [`CLAUDE.md`](../CLAUDE.md) §31.

- Terceiro microsserviço de negócio (o fallback é rota lógica do próprio `inventory-service`).
- Ações `REDIRECT` e `PARALLELIZE` (discutidas na teoria, fora da implementação).
- Kubernetes, service mesh, API gateway, escalabilidade horizontal real.
- Consenso distribuído, Byzantine Fault Tolerance, relógio de Lamport, ordenação total global.
- `durable execution` completa / checkpoint-restart transparente do orquestrador.
- Múltiplos agentes, DAG dinâmico executável.
- Airflow como componente executado (só referência conceitual).
- Plataforma especializada de Chaos Engineering (Chaos Mesh, LitmusChaos).
- Substituir RabbitMQ, SQLite ou transformar Celery em decisor.
- Autocorreção de decisão inválida do LLM ou uso de Rules como fallback oculto do LLM.
- Medição direta de consumo energético.

## 1.7 Piloto técnico × coleta definitiva

O **piloto técnico** valida arquitetura, contratos, fluxo, persistência, rastreabilidade e
executabilidade. **Não integra a amostra** do TCC.

| | Piloto | Coleta definitiva |
|---|---|---|
| `phase` | `PILOT` | `EXPERIMENT` |
| `eligible_for_sample` | `false` | `true` |
| Parâmetros | Provisórios, ajustáveis | Congelados e versionados |
| Diretório | `data/pilot/` | `data/experiment/` |

Nunca converter retroativamente uma execução de piloto em dado da amostra
(`piloto-do-experimento.md` §27–28, §35.1).

## 1.8 Limitações metodológicas (resumo)

- Arquitetura simulada com dois microsserviços: não generaliza para ambientes produtivos
  de larga escala.
- Injeção de falhas por scripts Python + controle de Docker Compose: diversidade e
  profundidade limitadas frente a plataformas de Chaos Engineering.
- Não determinismo do LLM: `temperature = 0` reduz a variabilidade, mas não a elimina — a
  repetição experimental continua necessária.
- `SYSTEM_STATE` é um *snapshot*: pode envelhecer entre a construção do estado e a execução
  da ação (especialmente durante a inferência LLM). Os `timestamps` permitem observar a
  defasagem, sem protocolo formal de *snapshot* distribuído.
- Overhead decisório da abordagem LLM depende de modelo, quantização, runtime e hardware:
  os valores absolutos valem para a configuração documentada, não como custo universal.

Detalhes em [11-requisitos.md](11-requisitos.md) e no capítulo 4.6 da metodologia.
