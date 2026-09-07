# CLAUDE.md

## 1. Finalidade deste arquivo

Este arquivo fornece contexto e regras de trabalho para qualquer agente Claude utilizado no desenvolvimento deste repositório.

O projeto é um experimento acadêmico de TCC sobre orquestração de tarefas assíncronas em uma arquitetura simulada de microserviços, comparando dois mecanismos de decisão:

1. `RulesDecisionEngine` — abordagem tradicional, determinística e baseada em regras previamente definidas;
2. `LLMDecisionEngine` — abordagem baseada em um modelo de linguagem executado localmente.

O objetivo do agente ao atuar neste repositório é implementar, revisar ou auxiliar o desenvolvimento exatamente dentro do escopo experimental já definido, evitando ampliar silenciosamente a arquitetura, modificar a variável experimental ou introduzir decisões que prejudiquem a comparabilidade entre Rules e LLM.

## 2. Documento de contexto obrigatório

Antes de realizar alterações arquiteturais, metodológicas ou que afetem o protocolo experimental, leia primeiro:

`piloto-do-experimento.md`

Esse documento contém o detalhamento consolidado do piloto técnico, incluindo stack, responsabilidades, fluxo Pedido → Estoque, arquitetura, contrato RabbitMQ, estados da tarefa, `SYSTEM_STATE`, espaço de ações, `RulesDecisionEngine`, política para decisões inválidas, configuração inicial do LLM, estrutura sugerida do projeto, requisitos funcionais e não funcionais, sequência de desenvolvimento, critérios do piloto e parâmetros ainda sujeitos a congelamento antes da coleta definitiva.

Em caso de dúvida entre uma escolha genérica de engenharia e uma definição já registrada em `piloto-do-experimento.md`, prevalece o documento do piloto, salvo instrução explícita do responsável pelo projeto.

## 3. Contexto acadêmico

Este repositório é parte de um trabalho acadêmico.

O experimento avalia o uso de um agente baseado em LLM na tomada de decisão para orquestração de tarefas assíncronas em microserviços.

A comparação deve preservar uma única diferença principal:

`RulesDecisionEngine` versus `LLMDecisionEngine`.

As duas abordagens devem compartilhar, tanto quanto possível:

- mesma arquitetura;
- mesmos microserviços;
- mesmos bancos;
- mesmo RabbitMQ;
- mesmo Celery;
- mesmo `StateBuilder`;
- mesmo `SYSTEM_STATE`;
- mesmo conjunto de ações;
- mesmo `DecisionValidator`;
- mesmo `DecisionExecutor`;
- mesmos limites operacionais;
- mesma instrumentação;
- mesmo dataset;
- mesma política de reset;
- mesma política de readiness;
- mesmos cenários experimentais;
- mesma coleta de métricas.

Não introduza diferenças de infraestrutura entre Rules e LLM sem justificativa metodológica explícita.

## 4. Escopo da arquitetura

A arquitetura possui dois microserviços de negócio.

### 4.1 `orders-service`

Responsabilidades principais:

- receber pedidos;
- validar pedidos;
- persistir pedidos em banco SQLite próprio;
- criar e acompanhar tarefas;
- coordenar o fluxo do pedido;
- construir ou acionar o `StateBuilder`;
- selecionar o motor de decisão;
- validar decisões;
- executar ações validadas;
- publicar solicitações para Inventory;
- receber eventos de retorno;
- detectar timeout operacional;
- concluir ou abortar tarefas;
- produzir rastreabilidade.

Os seguintes componentes pertencem à camada de coordenação do `orders-service` e não são microserviços adicionais:

- `StateBuilder`;
- `DecisionEngine`;
- `RulesDecisionEngine`;
- `LLMDecisionEngine`;
- `DecisionValidator`;
- `DecisionExecutor`.

### 4.2 `inventory-service`

Responsabilidades principais:

- consumir solicitações de reserva;
- validar mensagens;
- detectar redelivery;
- garantir idempotência;
- realizar reserva simulada;
- persistir em SQLite próprio;
- executar rota primária ou fallback;
- publicar sucesso ou falha;
- produzir rastreabilidade.

### 4.3 Persistência

Cada microserviço possui seu próprio SQLite:

- `orders-service` → `orders.db`
- `inventory-service` → `inventory.db`

Não criar banco compartilhado.

Não permitir acesso direto de um microserviço ao banco do outro.

## 5. Stack definida

Utilize como base:

- Python
- FastAPI
- Docker
- Docker Compose
- RabbitMQ
- Celery
- SQLite
- Ollama

### FastAPI

Interface HTTP do `orders-service`.

### RabbitMQ

Broker de mensagens. RabbitMQ não decide políticas de orquestração.

### Celery

Mecanismo de execução de tarefas assíncronas sobre RabbitMQ. Celery não é o mecanismo de decisão experimental.

### SQLite

Persistência independente de cada microserviço.

### Ollama

Runtime local utilizado pelo `LLMDecisionEngine`.

### Apache Airflow

Não faz parte da execução do experimento.

Airflow e DAGs podem existir no referencial conceitual do TCC, mas:

- não adicionar Airflow ao `docker-compose`;
- não usar Airflow como baseline;
- não substituir `RulesDecisionEngine` por Airflow.

## 6. Fluxo funcional principal

O caminho funcional mínimo é:

`POST /orders`
→ `orders-service`
→ persistir pedido + tarefa
→ `StateBuilder`
→ `DecisionEngine`
→ `CONTINUE`
→ RabbitMQ
→ `inventory.primary`
→ `inventory-service`
→ persistir reserva
→ `STOCK_RESERVATION_SUCCEEDED`
→ `orders.events`
→ `orders-service`
→ `COMPLETED`

O primeiro objetivo de implementação é fazer esse fluxo funcionar de ponta a ponta antes de adicionar complexidade experimental.

## 7. Contrato de mensageria

Mensagens de negócio devem usar um envelope lógico versionado.

Campos centrais:

- `schema_version`
- `execution_id`
- `message_id`
- `task_id`
- `event_type`
- `event_seq`
- `attempt_number`
- `published_at`
- `decision_id`
- `target`
- `payload`

Exemplo conceitual:

```json
{
  "schema_version": "1.0",
  "execution_id": "PILOT_0001",
  "message_id": "MSG_0192",
  "task_id": "TASK_0187",
  "event_type": "STOCK_RESERVATION_REQUESTED",
  "event_seq": 2,
  "attempt_number": 1,
  "published_at": "2026-08-27T12:00:00.000Z",
  "decision_id": "DEC_0091",
  "target": "inventory.primary",
  "payload": {
    "order_id": "ORD_0187",
    "items": [
      {
        "sku": "SKU-001",
        "quantity": 2
      }
    ]
  }
}
```

## 8. Filas e targets

A topologia lógica inicial é:

- `inventory.primary`
- `inventory.fallback`
- `orders.events`
- `tasks.dlq`

Não inventar novos microserviços para representar fallback.

O fallback é uma rota lógica do próprio `inventory-service`.

## 9. Redelivery não é RETRY

Esta distinção é obrigatória.

### Redelivery do broker

A mesma mensagem é entregue novamente.

Preservar:

- `message_id`
- `task_id`
- `event_seq`
- `attempt_number`

### `RETRY` decidido pelo orquestrador

É uma nova tentativa lógica.

Usar:

- novo `message_id`;
- mesmo `task_id`;
- novo `event_seq`;
- `attempt_number + 1`.

Não delegar o `RETRY` experimental ao `autoretry` do Celery.

## 10. Idempotência

Implementar duas proteções conceitualmente distintas.

### Idempotência de transporte

Base: `message_id`.

Evita repetir efeito causado por redelivery da mesma mensagem.

### Idempotência de negócio

Base: `task_id`.

Evita que uma nova tentativa lógica produza uma segunda reserva caso a primeira já tenha sido efetivada.

Não assumir que `message_id` sozinho resolve todos os casos.

## 11. Estados da tarefa

Estados previstos:

- `PENDING`
- `DISPATCHED`
- `PROCESSING`
- `WAITING`
- `RETRYING`
- `FALLBACK_PROCESSING`
- `COMPLETED`
- `ABORTED`
- `DEAD_LETTERED`

Estados terminais:

- `COMPLETED`
- `ABORTED`
- `DEAD_LETTERED`

Estados externos simplificados do pedido:

- `PENDING`
- `COMPLETED`
- `FAILED`

Não adicionar novos estados sem necessidade clara e sem atualizar `piloto-do-experimento.md`.

## 12. `SYSTEM_STATE`

Rules e LLM devem receber o mesmo estado normalizado.

Campos esperados incluem, entre outros:

- `execution_id`
- `task_id`
- `state_id`
- `timestamp`
- `current_event_seq`
- `phase`
- `current_service`
- `current_target`
- `attempt_number`
- `max_attempts`
- `wait_count`
- `max_waits`
- `elapsed_ms`
- `service_status`
- `latency_ms`
- `last_result`
- `queue_size`
- `redelivered`
- `fallback_available`
- `fallback_used`
- `alternative_targets`
- `recent_events`

O histórico completo não deve ser enviado automaticamente ao LLM.

Use somente a janela de `recent_events` configurada para o experimento.

## 13. Espaço de ações

O espaço de ações executáveis está restrito a:

- `CONTINUE`
- `RETRY`
- `WAIT`
- `FALLBACK`
- `ABORT`

Não adicionar `REDIRECT` ou `PARALLELIZE` ao experimento atual.

Essas ações podem aparecer na discussão teórica, mas não fazem parte da implementação deste recorte.

## 14. Semântica das ações

### `CONTINUE`

Executar a próxima transição normal do fluxo.

### `RETRY`

Criar nova tentativa lógica no mesmo target.

### `WAIT`

Aguardar o intervalo configurado e reavaliar o estado.

Não incrementar `attempt_number`.

### `FALLBACK`

Trocar `inventory.primary` por `inventory.fallback`, quando permitido.

### `ABORT`

Encerrar a tarefa de forma controlada.

## 15. Limitação do fallback

`inventory.fallback` pertence ao mesmo `inventory-service`.

Portanto:

- falha localizada na rota primária → `FALLBACK` pode ser válido;
- `inventory-service` totalmente indisponível → `FALLBACK` não resolve.

No segundo caso, as ações válidas devem ser compatíveis com `WAIT` ou `ABORT`.

Não crie um terceiro serviço apenas para simular fallback.

## 16. `RulesDecisionEngine`

O `RulesDecisionEngine` deve ser:

- determinístico;
- explícito;
- auditável;
- baseado somente no `SYSTEM_STATE`;
- congelado antes da coleta definitiva.

A política geral já foi definida em `piloto-do-experimento.md`.

Não substituir a política por heurísticas novas sem atualizar a especificação.

Não usar regras diferentes entre execuções definitivas.

## 17. `max_attempts`

Use `max_attempts` e não `max_retries`.

Interpretação:

`max_attempts = 3` significa uma tentativa inicial e até duas novas tentativas.

Evite nomenclatura ambígua.

## 18. `LLMDecisionEngine`

O LLM deve atuar somente como mecanismo de seleção da próxima ação.

Não dar ao modelo acesso irrestrito ao sistema.

Não permitir que o modelo execute diretamente:

- shell;
- Docker;
- RabbitMQ;
- SQLite;
- arquivos do host;
- comandos arbitrários;
- código Python gerado dinamicamente.

O fluxo deve ser:

`SYSTEM_STATE`
→ prompt fixo
→ LLM
→ decisão estruturada
→ `DecisionValidator`
→ `DecisionExecutor`

## 19. Runtime e modelo inicial

Configuração inicial prevista para o piloto:

```yaml
runtime: ollama
model: llama3.1:8b
temperature: 0.0
top_p: 1.0
max_tokens: 64
seed: 42
request_timeout_seconds: 30
session_memory: false
stream: false
```

Atenção: `llama3.1:8b` é o modelo inicial do piloto.

Ele ainda pode ser alterado durante o piloto caso haja inviabilidade objetiva de hardware/runtime.

Depois do congelamento experimental:

- não trocar modelo;
- não trocar quantização;
- não trocar parâmetros;
- não trocar runtime;
- não alterar prompt.

## 20. LLM stateless

Cada decisão deve ser uma chamada independente.

Não manter histórico conversacional invisível entre decisões.

A continuidade deve vir exclusivamente do estado controlado produzido pelo `StateBuilder`.

## 21. Contrato de decisão

Rules e LLM devem retornar o mesmo schema.

Exemplo:

```json
{
  "action": "RETRY",
  "target": "inventory.primary",
  "reason_code": "TRANSIENT_RETRY"
}
```

O contrato não deve permitir instruções arbitrárias.

## 22. `DecisionValidator`

O mesmo validador deve ser usado para Rules e LLM.

Validar no mínimo:

- ação permitida;
- target permitido;
- `reason_code`;
- limite de tentativas;
- target do `RETRY`;
- disponibilidade do fallback;
- fallback ainda não utilizado;
- target correto do fallback;
- ausência de target em `WAIT`;
- ausência de target em `ABORT`;
- impossibilidade de agir sobre estado terminal.

Não criar um validador especial para o LLM.

## 23. Política para decisão inválida

Regra obrigatória:

`decisão inválida` → registrar → `ABORT`.

Não realizar:

- segunda inferência corretiva;
- autocorreção;
- fallback automático para Rules;
- escolha heurística de ação semelhante;
- execução parcial.

Uma decisão inválida é um resultado possível da abordagem.

## 24. Decisão inválida não significa execução inválida

Diferenciar rigorosamente.

### Decisão inválida do LLM

Resultado do mecanismo avaliado.

Exemplo:

- `validation.valid = false`
- `executed_decision = ABORT`
- `run_status = VALID`

Pode integrar a amostra.

### Falha da bancada experimental

Exemplos:

- RabbitMQ não inicializou;
- banco não foi restaurado;
- arquivo obrigatório corrompeu;
- Ollama não passou no readiness;
- configuração experimental incorreta.

Nesse caso:

`run_status = INVALID`

Não deve integrar a amostra.

## 25. Timeout do LLM

### Antes da execução

Ollama não passa no readiness: não iniciar repetição válida.

### Durante execução válida

Inferência excede o timeout:

`LLM_DECISION_TIMEOUT` → `ABORT`.

Registrar como comportamento da abordagem.

## 26. `DecisionExecutor`

O executor não decide.

Ele apenas executa ações já validadas.

Regra:

- `valid == true` → executar `proposed_decision`;
- `valid == false` → executar `ABORT / INVALID_DECISION`.

Não adicionar política alternativa no executor.

## 27. Rastreabilidade

Preservar correlação entre:

- `execution_id`
- `task_id`
- `state_id`
- `decision_id`
- `message_id`
- `event_seq`

Arquivos previstos:

- `states.jsonl`
- `task_events.jsonl`
- `decisions.jsonl`
- `microservices_logs.jsonl`
- `queue_metrics.csv`
- `container_stats.csv`
- `fault_events.jsonl`
- `execution_metadata.json`
- `invalid_runs.csv`

Não remover rastreabilidade para simplificar código.

## 28. Piloto técnico versus experimento

Separar fisicamente:

```text
data/
├── pilot/
└── experiment/
```

Metadados do piloto:

```json
{
  "phase": "PILOT",
  "eligible_for_sample": false
}
```

Metadados da coleta definitiva:

```json
{
  "phase": "EXPERIMENT",
  "eligible_for_sample": true
}
```

Nunca converter retroativamente um piloto em dado da amostra.

## 29. Sequência de implementação

Respeite esta ordem, salvo necessidade técnica clara.

1. Docker Compose + RabbitMQ + Orders + Inventory + dois SQLite + fluxo normal.
2. Contrato de mensagens + idempotência + redelivery + `event_seq`.
3. `StateBuilder` + `SYSTEM_STATE` + rastreabilidade.
4. `RulesDecisionEngine` + `DecisionValidator` + `DecisionExecutor`.
5. Ollama + `LLMDecisionEngine` + parser estruturado.
6. Scripts de falha + scripts de carga.
7. Instrumentação experimental completa.
8. Congelamento da configuração + coleta definitiva.

Não começar pela instrumentação mais complexa, caos ou LLM antes de o fluxo normal estar funcional.

## 30. O que o agente PODE fazer

O agente pode:

- implementar funcionalidades previstas;
- criar testes;
- refatorar código sem alterar a semântica experimental;
- melhorar organização do projeto;
- adicionar typing;
- adicionar validações;
- melhorar logs estruturados;
- implementar schemas;
- corrigir bugs;
- documentar decisões;
- criar scripts do piloto;
- criar testes de integração;
- criar testes de idempotência;
- criar testes de contrato;
- criar checks de readiness;
- criar reset do ambiente;
- criar instrumentação comum;
- sugerir melhorias compatíveis com o escopo;
- apontar inconsistências entre código e `piloto-do-experimento.md`.

Quando uma sugestão alterar uma decisão experimental, apresente-a primeiro como proposta e não a aplique silenciosamente.

## 31. O que o agente NÃO DEVE fazer

Não deve:

- adicionar terceiro microserviço de negócio;
- adicionar Kubernetes;
- adicionar service mesh;
- adicionar RAG;
- adicionar Airflow à execução;
- substituir RabbitMQ;
- substituir SQLite;
- criar banco compartilhado;
- transformar Celery em orquestrador;
- usar autoretry do Celery como substituto de `RETRY`;
- alterar o espaço de ações;
- reintroduzir `REDIRECT`;
- reintroduzir `PARALLELIZE`;
- permitir execução arbitrária pelo LLM;
- dar acesso shell direto ao LLM;
- adicionar memória conversacional ao LLM;
- fornecer mais contexto ao LLM que ao Rules sem justificativa;
- fornecer observabilidade diferente a Rules e LLM;
- criar validador diferente por abordagem;
- criar executor diferente por abordagem;
- corrigir automaticamente uma decisão inválida do LLM;
- usar Rules como fallback oculto do LLM;
- excluir uma execução apenas porque o LLM tomou uma decisão ruim;
- inserir dados de piloto na amostra;
- alterar parâmetros congelados durante a coleta;
- alterar o prompt entre repetições;
- alterar o modelo entre repetições;
- alterar regras do Rules entre repetições;
- mudar dataset durante a coleta;
- esconder falhas da abordagem;
- inventar métricas;
- inventar resultados experimentais;
- afirmar que algo foi medido sem dados reais;
- ampliar o escopo para multi-agent systems;
- implementar consenso/BFT;
- implementar relógio de Lamport apenas por interesse técnico;
- implementar durable execution completa sem decisão explícita do projeto;
- realizar mudanças metodológicas silenciosas.

## 32. Não otimizar prematuramente

Este é um ambiente experimental controlado.

Priorize:

- correção;
- clareza;
- rastreabilidade;
- repetibilidade;
- equivalência.

Antes de:

- performance extrema;
- abstrações genéricas;
- microframeworks internos;
- arquitetura distribuída adicional.

Não transforme o projeto em uma plataforma de produção.

## 33. Evitar overengineering

Não adicionar tecnologia não documentada.

Pergunte antes de introduzir algo que desvie do padrão ou que cause over-engineering, por exemplo:

- OpenTelemetry Collector complexo;
- service mesh;
- API Gateway;
- etcd;

A ausência dessas tecnologias pode ser intencional para preservar o recorte experimental.

## 34. Dependências

Antes de adicionar uma dependência:

1. confirme que ela resolve um requisito real;
2. prefira bibliotecas maduras;
3. evite dependência que altere o comportamento experimental;
4. registre sua função;
5. mantenha versão reproduzível quando o ambiente for congelado.

Não atualizar bibliotecas indiscriminadamente durante a fase de coleta.

## 35. Testes mínimos esperados

O projeto deve possuir testes para:

- fluxo normal: pedido → reserva → sucesso;
- redelivery: a mesma mensagem não duplica reserva;
- `RETRY`: nova tentativa possui novo `message_id`, mesmo `task_id`, novo `event_seq`;
- idempotência de negócio;
- `WAIT`: não incrementa `attempt_number`;
- `FALLBACK`: troca para `inventory.fallback` somente quando admissível;
- `ABORT`: produz estado terminal controlado;
- validação de decisões inválidas;
- política de decisão inválida → `ABORT`;
- equivalência: Rules e LLM passam pelo mesmo Validator e Executor.

## 36. Logs

Prefira logs estruturados.

Todo evento relevante deve permitir correlação.

Campos úteis quando aplicáveis:

- `execution_id`
- `task_id`
- `message_id`
- `event_seq`
- `state_id`
- `decision_id`
- `service`
- `event_type`
- `timestamp`

## 37. Timestamps

Use timestamps consistentes e normalizados.

Preferência: UTC, ISO 8601.

Exemplo:

`2026-08-27T12:00:00.000Z`

Não misturar timezone local e UTC nos artefatos experimentais.

## 38. Código

Princípios:

- funções pequenas;
- responsabilidades explícitas;
- tipos claros;
- contratos centralizados;
- evitar estado global;
- evitar comportamento implícito;
- evitar retries escondidos;
- evitar fallback escondido;
- evitar exceções engolidas;
- registrar erro experimental relevante.

Não utilizar abstrações que tornem difícil identificar:

- o que o decisor observou;
- o que decidiu;
- o que foi validado;
- o que foi executado.

## 39. Configuração

Parâmetros experimentais devem convergir para uma fonte de verdade, preferencialmente:

`config/experiment_config.yml`

Não espalhar limiares importantes em constantes diferentes pelo código.

Durante o piloto, parâmetros podem ainda ser provisórios.

Após o congelamento, devem permanecer estáveis.

## 40. Parâmetros provisórios

Os valores iniciais definidos no piloto não devem ser interpretados automaticamente como valores finais da amostra.

Exemplos:

- `inventory_timeout_ms`
- `max_attempts`
- `retry_delay_ms`
- `wait_delay_ms`
- `max_waits`
- `queue_high_watermark`
- `recent_events_limit`
- `task_deadline_ms`
- modelo LLM

Mudanças são permitidas no piloto quando tecnicamente justificadas.

Após o congelamento experimental, não alterar sem invalidar a comparabilidade.

## 41. Cenários experimentais

O ambiente deverá suportar, em fase posterior:

- normal;
- sobrecarga;
- falha intermitente;
- timeout;
- dados inconsistentes;
- recuperação pós-falha.

Não implemente todos antes do fluxo normal funcionar.

Falhas devem ser reproduzíveis e controláveis.

Não utilizar perturbações aleatórias não registradas.

## 42. Métricas

A arquitetura deve permitir medir, entre outras:

- latência média;
- latência P95;
- throughput;
- taxa de falhas/erros;
- tempo de recuperação;
- CPU;
- RAM;
- tamanho de fila;
- tempo de decisão;
- tempo de inferência LLM;
- número de decisões;
- tokens, quando fornecidos confiavelmente.

Não fabricar métricas.

Não calcular resultados acadêmicos com dados de piloto como se fossem da amostra definitiva.

## 43. Mudanças que exigem revisão antes de implementação

Se uma solicitação implicar qualquer um dos itens abaixo, não aplique automaticamente sem sinalizar o impacto:

- alterar número de microserviços;
- trocar broker;
- trocar banco;
- mudar o fluxo Pedido → Estoque;
- adicionar nova ação;
- remover uma ação;
- mudar semântica de `RETRY`;
- mudar semântica de `WAIT`;
- mudar semântica de `FALLBACK`;
- mudar política de decisão inválida;
- mudar contrato do `SYSTEM_STATE`;
- mudar Rules;
- mudar Validator;
- mudar Executor;
- mudar modelo/runtime após congelamento;
- mudar dataset;
- mudar limiares durante a coleta;
- alterar instrumentação de apenas uma abordagem.

Informe que a alteração possui impacto metodológico e precisa ser refletida na documentação acadêmica.

## 44. Integridade acadêmica

Nunca:

- invente referências;
- invente experimentos;
- invente resultados;
- invente valores estatísticos;
- invente versões de software não verificadas;
- declare teste executado sem execução real;
- declare métrica coletada sem arquivo/dado correspondente.

Quando produzir código experimental, diferencie claramente:

- implementado;
- testado;
- planejado;
- provisório.

## 45. Documentação acadêmica

O código deve permanecer coerente com a metodologia.

Se a implementação divergir de `piloto-do-experimento.md` ou do capítulo metodológico, sinalize a divergência.

Não altere silenciosamente o comportamento para melhorar o sistema se isso modificar o experimento.

O repositório é também um artefato de reprodutibilidade científica.

## 46. Prioridade em caso de conflito

Quando houver conflito entre instruções técnicas, use a seguinte ordem:

1. instrução explícita atual do responsável pelo projeto;
2. decisões metodológicas já congeladas para a coleta;
3. `piloto-do-experimento.md`;
4. este `CLAUDE.md`;
5. convenções existentes do repositório;
6. preferência genérica de engenharia.

Se a instrução atual contradizer uma decisão experimental já congelada, informe o impacto antes de executar a mudança.

## 47. Regra de atuação

Antes de uma alteração relevante, verifique:

1. Isso altera a variável experimental?
2. Isso dá vantagem a Rules ou LLM?
3. Isso muda o `SYSTEM_STATE`?
4. Isso muda o espaço de ações?
5. Isso muda o Validator?
6. Isso muda o Executor?
7. Isso muda retry/fallback/timeout?
8. Isso altera a rastreabilidade?
9. Isso altera uma configuração já congelada?
10. Isso precisa ser refletido no TCC?

Se a resposta for sim para qualquer item, trate a mudança como metodologicamente relevante.

## 48. Objetivo imediato

O objetivo imediato do repositório é produzir um piloto técnico estável.

Primeiro:

Docker Compose + RabbitMQ + `orders-service` + `orders.db` + `inventory-service` + `inventory.db` + mensagem assíncrona + reserva + evento de retorno + pedido `COMPLETED`.

Depois:

idempotência → `StateBuilder` → rastreabilidade → `RulesDecisionEngine` → Validator → Executor → `LLMDecisionEngine` → falhas → carga → instrumentação → congelamento → coleta definitiva.

Não inverter desnecessariamente essa ordem.

## 49. Resumo operacional para Claude

- LEIA `piloto-do-experimento.md`.
- MANTENHA dois microserviços.
- NÃO crie um terceiro serviço.
- USE RabbitMQ + Celery para execução assíncrona.
- NÃO transforme Celery em decisor.
- COMPARE `RulesDecisionEngine` versus `LLMDecisionEngine`.
- USE o mesmo `SYSTEM_STATE`.
- USE as mesmas cinco ações: `CONTINUE`, `RETRY`, `WAIT`, `FALLBACK`, `ABORT`.
- USE o mesmo Validator.
- USE o mesmo Executor.
- TRATE decisão inválida como `ABORT`.
- NÃO corrija automaticamente o LLM.
- NÃO use Rules como fallback oculto do LLM.
- MANTENHA rastreabilidade.
- SEPARE pilot de experiment.
- NÃO altere configuração congelada durante a coleta.
- NÃO amplie o escopo sem necessidade.
- SINALIZE qualquer mudança com impacto metodológico.
