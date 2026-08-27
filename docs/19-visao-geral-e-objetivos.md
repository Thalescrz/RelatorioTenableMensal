# Visão geral e objetivos

## Problema resolvido

O projeto automatiza relatórios técnicos recorrentes de vulnerabilidades sem
perder o padrão editorial dos documentos produzidos manualmente. A aplicação
coleta dados da Tenable, aplica regras temporais reproduzíveis, normaliza
identidades, calcula indicadores, recupera histórico do mesmo cliente e publica
documentos Word auditáveis.

O foco é uma carteira com clientes heterogêneos. O núcleo comum permanece estável,
enquanto módulos adicionais são ativados pelo perfil de cada cliente.

## Objetivos

- gerar o relatório do mês anterior automaticamente no primeiro dia do mês;
- permitir relatórios pontuais com período padrão ou escolhido pelo analista;
- preservar textos, títulos, tabelas e ordem editorial aprovados nos modelos;
- separar o documento-base das análises customizadas;
- gerar recortes independentes por TAG sem alterar os números gerais;
- gerar o relatório Cloud opcional na mesma execução, sem torná-lo dependência dos documentos VM;
- manter histórico compacto suficiente para comparações futuras;
- permitir validação dos números na Tenable por meio de filtros curtos;
- operar múltiplos clientes com progresso, alertas e retentativas controladas;
- proteger credenciais e dados sensíveis durante desenvolvimento e publicação.

## Entregáveis

### Relatório-base geral

Contém o conteúdo comum entre os modelos aprovados, incluindo indicadores gerais,
principais ativos vulneráveis, Top 5 detalhado de vulnerabilidades VM não mitigadas
e Top 5 detalhado de vulnerabilidades WEB quando o WAS estiver disponível. A coluna
`Exploitable` dos principais ativos contabiliza findings cujo indicador geral de
exploração esteja ativo. A coluna `Output` nos detalhamentos é opcional.

### Relatório de inteligência e customizações

Reúne os módulos adicionais habilitados para o cliente: quadros analíticos atuais,
OWASP, exploração por framework e séries históricas. Um módulo sem ocorrências deve
explicar que não houve identificação no mês; ausência de dados não deve parecer uma
falha de renderização.

Comparativos temporais só são produzidos quando existe referência compatível. O
primeiro relatório continua útil e apresenta os blocos que dependem apenas do mês
corrente.

### Relatório operacional por TAG

É um recorte VM compacto para uma TAG selecionada. Repete os módulos operacionais
necessários — inclusive principais ativos, visão geral das vulnerabilidades
mitigadas, não mitigadas e ressurgidas e detalhamento das vulnerabilidades — usando
o padrão completo de hosts do projeto. Pode incluir tabelas e gráficos do
comparativo da mesma TAG no tempo.

O nome do arquivo identifica cliente, categoria/valor da TAG e período. A seleção
de TAGs para gerar documentos é independente da seleção de TAGs que recebem o
comparativo temporal.

### Relatório Tenable Cloud Security

É um documento próprio, habilitado por cliente e gerado junto com a execução
normal. A coleta GraphQL cria uma fotografia Cloud independente do dataset VM. Na
homologação, a mesma fotografia produz o **Modelo Base** e o **Modelo Ampliado**;
a configuração final mantém `base`, `expanded` ou temporariamente `comparison`.

O conteúdo comum inclui principais hosts e imagens, Top 5 de CVEs críticas com
detalhamento e ativos afetados, Top 10 com correção disponível e dashboard. O
modelo ampliado acrescenta resumo executivo, componentes, postura quando suportada,
envelhecimento, remediação, inventário e evolução mensal. Fontes não licenciadas ou
indisponíveis são omitidas ou sinalizadas; ausência de dado não é convertida em
zero.

## Princípios de negócio

1. O relatório geral sempre representa o ambiente geral do cliente no período.
2. TAG é escopo de documento adicional, não filtro da coleta ou do relatório geral.
3. Comparação por TAG significa a mesma TAG em dois momentos, nunca TAG contra TAG.
4. Ativos e findings são vinculados por UUID; IP e hostname não são identidade.
5. `Informational` não entra nos indicadores atuais.
6. Texto editorial aprovado é preservado; somente campos dinâmicos são atualizados.
7. Um DOCX só é registrado como publicado após passar pelas validações da execução.
8. A referência automática `MAIN` pode ser substituída pelo analista.
9. Falha Cloud preserva os demais documentos e permite retentativa somente do componente Cloud.

## Limites atuais

- O Cloud representa uma fotografia do instante da coleta. Um período histórico só
  é reproduzido exatamente quando existe fotografia Cloud compatível preservada.
- A tradução está preparada como fronteira de apresentação, porém não existe
  provedor automático integrado; textos longos não devem ser enviados a serviços
  externos sem autorização e política de privacidade.
- O WAS depende da disponibilidade e das permissões do tenant. Sua ausência não
  bloqueia a entrega VM.
- A interface é local e simples; autenticação multiusuário e publicação remota não
  fazem parte do escopo atual.

## Critério de sucesso

Uma execução bem-sucedida apresenta período e cliente corretos, reconcilia
contagens do dataset, gera apenas os módulos habilitados e suportados, registra os
documentos no PostgreSQL, define a referência automática quando aplicável e remove
os dados intermediários pesados sem eliminar o histórico compacto VM, TAG ou Cloud.
