# Documentação, instruções de agentes e skills — Especificação de design

**Data:** 2026-08-23  
**Status:** aprovado para planejamento  
**Escopo:** documentação consolidada, atualização de documentos existentes, arquivos `AGENTS.md` e skills versionadas do projeto

## 1. Contexto

O repositório possui documentação extensa organizada pelas fases de construção. Esses
arquivos preservam decisões e evidências importantes, mas não formam um manual atual
único. O `README.md` acumulou explicações operacionais, enquanto algumas páginas de
fase ainda descrevem PostgreSQL, histórico, interface, agendamento ou WAS como trabalho
futuro. Também existem divergências sobre retenção e sobre o destino dos comparativos
por TAG.

Não há atualmente um índice de documentação, instruções `AGENTS.md` por escopo nem
skills versionadas que orientem as tarefas recorrentes de operação e validação.

## 2. Objetivos

1. Explicar de forma atual como a solução funciona e quais problemas resolve.
2. Documentar dados coletados, campos normalizados, métricas derivadas e limites das
   fontes VM, WAS, TAGs e Cloud Security.
3. Criar um runbook para operação pela interface e, quando necessário, pela CLI.
4. Tornar explícitas as fronteiras arquiteturais e as regras de contribuição.
5. Orientar agentes sem repetir todo o manual em cada contexto.
6. Preservar documentos históricos por fase, corrigindo fatos que hoje contradizem a
   implementação.

## 3. Não objetivos

- alterar métricas, coleta, interface ou documentos Word;
- iniciar exports reais ou acessar credenciais;
- implementar Tenable Cloud Security ou tradução;
- reescrever ou apagar evidências históricas das fases;
- transformar `AGENTS.md` em duplicação da documentação funcional.

## 4. Fonte de verdade

A precedência documental será:

1. código e testes executáveis;
2. documentação consolidada atual;
3. contratos de domínio e operação ainda vigentes;
4. documentos históricos de fase, especificações e planos.

O índice identificará o propósito e o estado de cada documento. Evidências autenticadas
permanecem históricas e datadas; números de uma coleta antiga nunca serão apresentados
como estado atual do produto.

## 5. Estrutura documental

Serão criados:

- `docs/README.md`: índice, fonte de verdade e trilhas de leitura;
- `docs/19-visao-geral-e-objetivos.md`: objetivo, usuários, entregáveis, capacidades e
  limites atuais;
- `docs/20-arquitetura-e-fluxo-de-dados.md`: componentes, fluxo, fronteiras e ciclo de
  vida dos dados;
- `docs/21-catalogo-de-dados-e-metricas.md`: dados VM/WAS/TAG, campos normalizados,
  datasets, métricas, rankings, disponibilidade e dados sensíveis;
- `docs/22-guia-operacional.md`: instalação, configuração, interface, períodos,
  execução, retentativas, PostgreSQL, armazenamento e solução de problemas;
- `docs/23-guia-de-desenvolvimento.md`: estrutura do código, extensão, testes e gates.

O `README.md` continuará como porta de entrada, com visão curta, início rápido e links
para as páginas consolidadas. Exemplos extensos devem ficar no guia operacional.

## 6. Atualização dos documentos existentes

As correções serão factuais e localizadas:

- `02-catalogo-apis-tenable.md`: estado atual de propriedades seletivas, retomada por
  chunks, timeouts separados, cancelamento seguro e WAS opcional;
- `05-historico-regras-criticas-e-traducao.md`: PostgreSQL como backend operacional,
  predecessor `MAIN` imediato e histórico compacto;
- `12-perfis-e-variacoes-fase7.md`: remover o gate futuro de WAS e apontar as
  capacidades atuais;
- `13-was-fase8.md`: TAGs não afetam WAS nem os documentos gerais; histórico,
  agendamento e interface já existem;
- `14-historico-e-tendencias-fase9.md`: comparativos por TAG pertencem ao documento da
  TAG e não ao DOCX customizado geral;
- `15-orquestracao-e-distribuicao-fase10.md`: incluir documentos por TAG e corrigir a
  política de reciclagem;
- `16-postgresql-migracao-e-operacao.md`: separar histórico permanente de staging
  efêmero e ajustar backup/recuperação;
- `17-interface-web-mvp.md`: registrar controles atuais de `MAIN`, exclusão/restauração,
  TAGs, propriedades seletivas, cancelamento, backfill e limpeza;
- `18-main-retentativas-inteligencia-operacao.md`: substituir horizontes antigos pela
  coleta efêmera e retenção atual.

Os demais documentos serão catalogados no índice como contrato vigente, evidência
histórica ou protocolo de referência. Afirmações históricas continuam datadas.

## 7. Instruções para agentes

Serão criados quatro níveis:

- `AGENTS.md`: comandos seguros, fonte de verdade, arquitetura, segurança, invariantes
  de negócio e validação mínima;
- `src/tenable_reports/AGENTS.md`: dependências permitidas entre domínio, aplicação,
  infraestrutura, apresentação e web;
- `tests/AGENTS.md`: TDD, fixtures sanitizadas, isolamento e proibição de API real;
- `clients/AGENTS.md`: perfis declarativos, IDs estáveis, TAGs e separação de segredos.

Arquivos específicos complementam o arquivo raiz e não repetem instruções gerais.

## 8. Skills do projeto

As skills ficarão em `.agents/skills`, permitindo versionamento junto ao código e
descoberta no contexto do projeto.

### 8.1 `operating-tenable-reports`

Usada quando a tarefa envolve subir a interface, configurar clientes, testar conexão,
gerar relatórios, escolher períodos, acompanhar exports, repetir falhas, administrar
`MAIN` ou limpar staging. O `SKILL.md` será curto e encaminhará detalhes para um
runbook de referência. Toda operação que inicia coleta real continua exigindo
autorização explícita.

### 8.2 `validating-tenable-report-data`

Usada para conferir contagens, explicar filtros, comparar relatório e plataforma,
investigar tabelas vazias ou validar métricas VM/WAS/TAG. A referência conterá
semântica temporal, grão, fonte e regra de ranking das tabelas, incluindo a distinção
entre `Last Seen`, `Last Fixed` e `Resurfaced`.

Convenções puramente internas ao repositório ficarão em `AGENTS.md`, não nas skills.

## 9. Segurança e privacidade

- Nenhum `.env`, chave, senha, hostname, IP, URI interna ou nome de pessoa será
  incorporado à documentação.
- Exemplos usarão clientes e identificadores fictícios.
- Comandos de leitura serão diferenciados de comandos que iniciam exports reais.
- A skill operacional não autorizará coleta, cancelamento ou exclusão por si só.
- `Plugin Output` será documentado como opt-in e potencialmente sensível.

## 10. Validação

A implementação terá testes determinísticos para:

- presença e frontmatter das duas skills;
- resolução das referências declaradas nas skills;
- existência dos `AGENTS.md` por escopo;
- links Markdown locais dos arquivos novos e alterados;
- ausência de placeholders de scaffold;
- exemplos que não contenham nomes de credenciais reais.

Cada skill será criada e validada separadamente. Para skills de referência, os testes
de aplicação usarão cenários de recuperação de informação: escolher período correto,
identificar a fonte de uma métrica, distinguir filtro de mitigadas e decidir se uma
TAG pode afetar o relatório geral. A validação estrutural usará também o
`quick_validate.py` fornecido pelo criador de skills.

A suíte Python completa será executada ao final. Nenhuma API real nem servidor será
necessário para validar esta entrega.

## 11. Critérios de aceite

- um analista novo consegue entender objetivo, capacidades, limites e fluxo sem ler
  os planos históricos;
- um desenvolvedor encontra o módulo correto e os testes necessários;
- um agente identifica rapidamente regras temporais, de TAG, histórico e segurança;
- o catálogo diferencia dados coletados, normalizados, derivados, históricos e não
  implementados;
- documentos existentes não contradizem PostgreSQL, retenção efêmera, WAS, interface
  ou relatórios por TAG;
- links, skills e suíte de testes passam offline.
