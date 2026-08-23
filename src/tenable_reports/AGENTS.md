# Instruções para o código da aplicação

## Arquitetura

- `domain` contém modelos e regras puras; não importa HTTP, PostgreSQL, DOCX ou UI.
- `application` coordena casos de uso e trabalha por contratos explícitos.
- `infrastructure` adapta Tenable, PostgreSQL e formatos persistidos.
- `presentation` renderiza datasets prontos; não recalcula métricas de domínio.
- `webapp` orquestra a experiência local sem duplicar regras de negócio.

Mantenha dependências apontando para dentro. Se uma métrica alimenta mais de um
consumidor, defina-a no dataset ou domínio e reutilize o resultado.

## Alterações obrigatoriamente testadas

Cubra primeiro por teste qualquer mudança em período, estado, identidade, ranking,
exploração, TAG, histórico, retenção, publicação, export ou fallback. Clientes HTTP
devem aceitar transportes simulados; testes unitários nunca acessam a rede.

## Tenable

- Exporte o ambiente geral antes de qualquer recorte por TAG.
- Considere o job finalizado somente com estado `FINISHED` e chunks tratados.
- Persista chunks assim que disponíveis e mantenha retomada idempotente.
- Cancele automaticamente apenas job criado pela execução atual e sem progresso.
- Não cancele automaticamente job fornecido, reutilizado ou retomado.
- Propriedades seletivas são opt-in por cliente, após equivalência comprovada.
- Fallback para payload completo é único e restrito a HTTP 400 ou contrato
  incompleto; não esconda autenticação, rate limit ou timeout.
- WAS é fluxo separado e best effort.

## Persistência e publicação

Nova mudança PostgreSQL recebe migration numerada; não edite migration já publicada.
Documento só entra no registro após validação. Preserve a identidade do `MAIN` e a
compatibilidade da mesma TAG. Limpeza de staging precisa ser posterior ao DOCX e ao
histórico compacto confirmados.

## Apresentação

Não invente conteúdo editorial. Datas dinâmicas devem preservar estilos, runs,
seções, cabeçalhos e rodapés. `Plugin Output` é opcional e sensível. Um módulo vazio
deve exibir a mensagem de ausência aprovada, não sumir como se houvesse erro.

## Erros e logs

Classifique falhas conhecidas e indique se são retentáveis. Eventos de progresso
devem incluir identidade suficiente para operação, mas nunca secrets ou conteúdo
sensível desnecessário.
