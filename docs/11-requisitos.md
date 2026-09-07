# 11 — Requisitos

> Fonte: `piloto-do-experimento.md` §29–30 (consolidação de engenharia das definições
> metodológicas). A metodologia não apresenta originalmente uma especificação formal de
> requisitos numerados — a numeração abaixo é do documento de piloto.

## 11.1 Requisitos funcionais

| ID | Requisito | Detalhe | Doc relacionado |
|---|---|---|---|
| RF-001 | Criar pedido | Operação para criação de pedido com lista de itens e quantidades (`POST /orders`) | [04](04-contrato-mensageria.md), [07](07-diagramas-uml.md) |
| RF-002 | Validar pedido | `orders-service` valida a solicitação antes de iniciar a tarefa | [02](02-arquitetura.md) |
| RF-003 | Persistir pedido | Pedido persistido **exclusivamente** no SQLite do `orders-service` | [08](08-modelo-de-dados-mer.md) |
| RF-004 | Criar tarefa | Tarefa associada ao pedido, com `task_id` único | [05](05-maquina-de-estados.md) |
| RF-005 | Publicar reserva de forma assíncrona | Solicitação ao `inventory-service` por RabbitMQ; sem chamada síncrona direta para a etapa de negócio | [04](04-contrato-mensageria.md) |
| RF-006 | Consumir solicitação de reserva | `inventory-service` consome a mensagem da fila correspondente | [04](04-contrato-mensageria.md) |
| RF-007 | Persistir reserva | Reserva simulada persistida **exclusivamente** no SQLite do `inventory-service` | [08](08-modelo-de-dados-mer.md) |
| RF-008 | Garantir idempotência de mensagem | Detectar redelivery por `message_id` | [04 §4.7.1](04-contrato-mensageria.md) |
| RF-009 | Garantir idempotência de negócio | Impedir mais de uma reserva efetiva para o mesmo `task_id` | [04 §4.7.2](04-contrato-mensageria.md) |
| RF-010 | Publicar resultado da reserva | `inventory-service` publica sucesso/falha para Orders | [04 §4.6](04-contrato-mensageria.md) |
| RF-011 | Processar retorno do estoque | Orders consome eventos de Inventory e atualiza tarefa e pedido | [07 §7.4.1](07-diagramas-uml.md) |
| RF-012 | Identificar sequência lógica | Todos os eventos de uma tarefa têm `event_seq` monotonicamente crescente | [04 §4.3–4.4](04-contrato-mensageria.md) |
| RF-013 | Construir estado operacional | `StateBuilder` constrói um `SYSTEM_STATE` normalizado a cada ponto de decisão | [06 §6.2](06-modelo-de-decisao.md) |
| RF-014 | Identificar o snapshot | Cada estado apresentado ao decisor tem `state_id` único | [06 §6.2](06-modelo-de-decisao.md) |
| RF-015 | Limitar histórico de contexto | `recent_events` contém só a janela `recent_events_limit` | [06 §6.2.3](06-modelo-de-decisao.md) |
| RF-016 | Disponibilizar duas estratégias de decisão | Selecionar `RulesDecisionEngine` ou `LLMDecisionEngine` | [02 §2.4](02-arquitetura.md) |
| RF-017 | Utilizar contrato de decisão único | Ambos retornam o mesmo schema (`action`/`target`/`reason_code`) | [06 §6.4](06-modelo-de-decisao.md) |
| RF-018 | Implementar `CONTINUE` | Continuar o fluxo normal quando a ação for validada | [06 §6.3](06-modelo-de-decisao.md) |
| RF-019 | Implementar `RETRY` | Nova tentativa lógica no mesmo target, respeitando `max_attempts` | [06 §6.3](06-modelo-de-decisao.md) |
| RF-020 | Implementar `WAIT` | Aguardar o período configurado e reavaliar, **sem** incrementar `attempt_number` | [06 §6.3](06-modelo-de-decisao.md) |
| RF-021 | Implementar `FALLBACK` | Trocar `inventory.primary` por `inventory.fallback` quando admissível | [06 §6.5](06-modelo-de-decisao.md) |
| RF-022 | Implementar `ABORT` | Encerrar controladamente a tarefa | [05](05-maquina-de-estados.md) |
| RF-023 | Detectar timeout operacional | Detectar ausência de evento de conclusão do Inventory dentro de `inventory_timeout_ms` | [04 §4.8](04-contrato-mensageria.md) |
| RF-024 | Aplicar política determinística Rules | Regras e prioridade fixas durante o experimento definitivo | [06 §6.6](06-modelo-de-decisao.md) |
| RF-025 | Consultar LLM local | `LLMDecisionEngine` consulta o Ollama com o `SYSTEM_STATE` corrente | [06 §6.8](06-modelo-de-decisao.md) |
| RF-026 | Operar LLM sem memória conversacional | Cada ponto de decisão LLM é chamada independente | [06 §6.8](06-modelo-de-decisao.md) |
| RF-027 | Exigir resposta estruturada | Saída do LLM analisada como objeto de decisão estruturado | [06 §6.8](06-modelo-de-decisao.md) |
| RF-028 | Validar decisões | Toda decisão passa pelo `DecisionValidator` comum | [06 §6.9](06-modelo-de-decisao.md) |
| RF-029 | Abortar decisão inválida | Registrar e transformar em `ABORT`, sem autocorreção e sem fallback para Rules | [06 §6.11](06-modelo-de-decisao.md) |
| RF-030 | Encaminhar mensagens não processáveis à DLQ | Mensagens que excedem a política podem ir para `tasks.dlq` | [04 §4.1](04-contrato-mensageria.md) |
| RF-031 | Registrar estados | Gerar `states.jsonl` | [10 §10.3](10-rastreabilidade-e-metricas.md) |
| RF-032 | Registrar eventos | Gerar `task_events.jsonl` | [10 §10.3](10-rastreabilidade-e-metricas.md) |
| RF-033 | Registrar decisões | Gerar `decisions.jsonl` | [10 §10.3](10-rastreabilidade-e-metricas.md) |
| RF-034 | Registrar logs dos serviços | Logs estruturados dos microsserviços | [10 §10.2](10-rastreabilidade-e-metricas.md) |
| RF-035 | Registrar metadados da execução | Cada execução tem `execution_metadata.json` | [10 §10.3](10-rastreabilidade-e-metricas.md) |
| RF-036 | Distinguir piloto de amostra | Execuções de piloto com `eligible_for_sample = false` | [01 §1.7](01-visao-geral.md) |
| RF-037 | Suportar reset experimental | Procedimento que restaura RabbitMQ/DLQ e os dois SQLite ao estado inicial | [10 §10.6](10-rastreabilidade-e-metricas.md) |
| RF-038 | Suportar readiness | Verificar prontidão dos componentes antes de iniciar execução válida | [10 §10.6](10-rastreabilidade-e-metricas.md) |
| RF-039 | Suportar warm-up do LLM | Warm-up antes da janela principal de medição | [03 §3.5](03-stack-tecnologica.md) |
| RF-040 | Suportar cenários controlados | Normal, sobrecarga, falha intermitente, timeout, dados inconsistentes, recuperação pós-falha | [01 §1.5](01-visao-geral.md) |
| RF-041 | Suportar injeção de falhas | Cenários acionados por scripts Python e/ou controle do Docker Compose | [10 §10.2](10-rastreabilidade-e-metricas.md) |
| RF-042 | Coletar métricas | Implementação definitiva permite coletar as métricas da metodologia | [10 §10.4](10-rastreabilidade-e-metricas.md) |

## 11.2 Requisitos não funcionais

| ID | Requisito | Detalhe |
|---|---|---|
| RNF-001 | Equivalência experimental | Rules e LLM operam sobre a mesma topologia, serviços, filas, bancos e políticas comuns |
| RNF-002 | Isolamento da variável principal | A principal diferença entre as condições é o mecanismo de seleção da ação |
| RNF-003 | Isolamento de persistência | Cada microsserviço tem banco próprio, sem acesso direto ao banco do outro |
| RNF-004 | Comunicação assíncrona | A coordenação Pedido → Estoque usa RabbitMQ |
| RNF-005 | Repetibilidade | Código, configs, dataset, estado inicial, seeds e políticas registrados e estáveis na coleta |
| RNF-006 | Reprodutibilidade documental | Versões, commit, imagens, runtime, modelo, parâmetros e arquivos suficientes para reconstrução |
| RNF-007 | Rastreabilidade | Correlacionar `execution_id`, `task_id`, `state_id`, `decision_id`, `message_id`, `event_seq` |
| RNF-008 | Auditabilidade da decisão | Identificar: estado observado → decisão proposta → resultado da validação → ação executada |
| RNF-009 | Mesmo validador | Rules e LLM usam exatamente o mesmo `DecisionValidator` |
| RNF-010 | Mesmo executor | Rules e LLM usam exatamente o mesmo `DecisionExecutor` |
| RNF-011 | Espaço de ações equivalente | As duas abordagens têm o mesmo conjunto de ações executáveis |
| RNF-012 | Contexto controlado | Tamanho e política de `recent_events` iguais nas duas abordagens |
| RNF-013 | LLM stateless | O runtime não introduz memória conversacional implícita entre decisões |
| RNF-014 | Segurança de execução | O LLM não produz nem executa comandos arbitrários no ambiente |
| RNF-015 | Fail-safe de decisão | Ação inválida contida por validação determinística e término controlado |
| RNF-016 | Ordenação por tarefa | Não pressupor ordenação total global das mensagens |
| RNF-017 | `event_seq` ≠ relógio lógico | `event_seq` é sequência de aplicação por tarefa, não relógio de Lamport |
| RNF-018 | Idempotência | Reentregas e novas tentativas não duplicam efeitos persistentes |
| RNF-019 | Observabilidade equivalente | A coleta que alimenta o `StateBuilder` é equivalente para Rules e LLM |
| RNF-020 | Mensurabilidade | Arquitetura permite medir latência média/P95, throughput, taxa de erro, tempo de recuperação, CPU, RAM, fila, tempo de decisão, tempo de inferência, nº de decisões, tokens (quando confiáveis) |
| RNF-021 | Execução local controlada | Experimento executável localmente com Docker Compose e runtime LLM local |
| RNF-022 | Configuração versionada | Parâmetros experimentais com fonte de verdade versionada em `experiment_config.yml` |
| RNF-023 | Readiness obrigatório | Nenhuma execução definitiva inicia antes de os componentes obrigatórios passarem na verificação |
| RNF-024 | Reset entre repetições | Uma repetição não herda filas, DLQ, reservas ou estados transitórios da execução anterior |
| RNF-025 | Piloto fora da amostra | Dados de desenvolvimento, calibração e depuração não integram a amostra |
| RNF-026 | Modelo congelado durante a coleta | Modelo, versão/digest, quantização, runtime e parâmetros de geração constantes |
| RNF-027 | Prompt congelado | O template do prompt permanece constante durante a coleta definitiva |
| RNF-028 | Rules congelado | Política e limiares do `RulesDecisionEngine` constantes durante a coleta definitiva |
| RNF-029 | Instrumentação não altera a variável | Instrumentação comum; não concede a uma abordagem informação operacional adicional |
| RNF-030 | Delimitação de escopo | Não se compromete a implementar Kubernetes, escalabilidade horizontal real, consenso distribuído, BFT, ordenação total global, relógio de Lamport, durable execution completa, checkpoint/restart transparente, múltiplos agentes, DAG dinâmico executável, Airflow como componente experimental, plataforma de Chaos Engineering |

## 11.3 Cenários experimentais

> Fonte: metodologia §4.4, TABELA 12. Não implementar todos antes de o fluxo normal
> funcionar. Falhas reproduzíveis e controláveis; sem perturbações aleatórias não
> registradas.

| Cenário | Sinais alterados no `SYSTEM_STATE` | Distinção relevante |
|---|---|---|
| Normal | — (*steady state*) | Base de comparação |
| Sobrecarga | `queue_size`, `latency_ms`, eventualmente CPU/RAM | Pressão de fila sem indisponibilidade |
| Falha intermitente | `service_status`, `last_result`, `attempt_number` | Degradação **localizada** da rota primária → `FALLBACK` pode ser válido |
| Atraso / *timeout* | `latency_ms`, `last_result`, `attempt_number` | Ausência de evento de conclusão dentro do limite |
| Dados inconsistentes | `last_result`, estado de validação | Leva a `ABORT` (`INVALID_DATA`) |
| Recuperação pós-falha | `service_status`, `queue_size`, `recent_events` | `inventory-service` **totalmente indisponível** → só `WAIT`/`ABORT`; mede tempo de recuperação |

## 11.4 Critério de conclusão do primeiro piloto técnico

> Fonte: `piloto-do-experimento.md` §32.

- [ ] `docker compose up` inicializa RabbitMQ, Orders e Inventory
- [ ] Orders responde ao endpoint de criação de pedido
- [ ] Orders persiste pedido e tarefa
- [ ] Uma mensagem versionada chega à `inventory.primary`
- [ ] Inventory processa a mensagem e persiste uma reserva
- [ ] Inventory publica evento de sucesso; Orders consome
- [ ] Pedido e tarefa terminam como `COMPLETED`
- [ ] A mesma mensagem reenviada **não** duplica a reserva
- [ ] `message_id`, `task_id` e `event_seq` aparecem nos registros
- [ ] `states.jsonl` registra o estado apresentado ao decisor
- [ ] `decisions.jsonl` registra a decisão
- [ ] `task_events.jsonl` permite reconstruir a trajetória
- [ ] `execution_metadata.json` contém `"phase": "PILOT"` e `eligible_for_sample: false`

**Não** é requisito deste primeiro marco: carga final, amostra estatística, definição de
`N_rep`, todas as falhas, análise comparativa, congelamento de todos os limiares.

## 11.5 Pendências para congelar antes da coleta definitiva

> `piloto-do-experimento.md` §34.

Versão exata do Python; versões das bibliotecas; versões/imagens Docker; versão do RabbitMQ;
versão do Celery; versão do Ollama; digest/identificador do modelo; quantização efetiva;
hardware; `QUEUE_HIGH_WATERMARK` final; `recent_events_limit = K` final; dataset final;
volume de carga; taxa de requisições; seeds; duração/intensidade de cada falha; `N_rep`;
ordem de execução Rules/LLM; política completa de reset; critérios automáticos de readiness;
hashes/commit da configuração congelada.
