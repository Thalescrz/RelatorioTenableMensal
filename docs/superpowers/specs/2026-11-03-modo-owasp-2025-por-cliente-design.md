# Modo OWASP 2025 por cliente

> **Registro de decisão:** este arquivo preserva o desenho aprovado antes da
> implementação. Ele não comprova sozinho o estado atual nem a conclusão das
> etapas. Consulte o [contexto atual](../../../CONTEXTO.md) e o
> [índice da documentação](../../README.md).

## Intenção

Permitir que cada cliente escolha como as tabelas OWASP Top 10:2025 do relatório
geral WAS serão formadas, preservando o comportamento atual como padrão e
oferecendo, quando solicitado, paridade com o filtro apresentado pela console
Tenable.

O modo escolhido afeta a formação das tabelas OWASP 2025. Ele não altera scans,
relatórios VM, TAG ou Cloud. No modo exclusivo, porém, a análise OWASP é uma
etapa obrigatória do componente WAS: sua falha impede a publicação do conteúdo
WAS daquela execução, sem bloquear os demais módulos.

`console_exclusive` não pode ser combinado com `scope.was.application_ids`
enquanto não houver equivalência comprovada do filtro remoto. A resposta da análise
já é agregada por plugin e não pode ser recortada localmente com segurança.

## Abordagens consideradas

1. **Consultar conforme a preferência do cliente — escolhida.** O perfil define
   `inclusivo` ou `exclusivo`. O fluxo atual permanece sem novas consultas no
   modo inclusivo; o modo exclusivo consulta a mesma fonte de análise usada pela
   console e persiste o resultado no dataset.
2. **Coletar os dois modelos sempre.** Facilitaria trocar o modo e regenerar um
   documento sem nova consulta, mas adicionaria chamadas e dados desnecessários
   para todos os clientes.
3. **Simular a seleção exclusiva com o detalhe do plugin.** Evitaria a rota de
   análise, mas não garantiria paridade com a console, principalmente para
   classificações múltiplas e findings com risco aceito.

A primeira abordagem foi escolhida por manter retrocompatibilidade, reduzir
consultas e preservar uma fonte verificável para o modo exclusivo.

## Configuração por cliente

O perfil do cliente terá um valor enumerado, e não um booleano ambíguo, com dois
estados:

- `inclusive`: padrão atual e valor assumido para perfis antigos; um plugin entra
  em uma categoria quando ela estiver entre suas classificações OWASP 2025;
- `console_exclusive`: um plugin entra em uma categoria somente quando a consulta
  exclusiva da console o retornar para aquela categoria.

No painel de gerenciamento, a configuração ficará junto das opções WAS, com duas
alternativas visíveis:

- **Inclusivo — qualquer associação com a categoria**;
- **Exclusivo — mesma seleção da console Tenable**.

A opção exclusiva só será aplicável quando WAS estiver habilitado. Criar, editar
e reabrir um cliente deve conservar exatamente a seleção salva.

Alterar o modo modifica permanentemente o perfil do cliente e passa a valer para
as coletas seguintes. Não haverá substituição temporária somente para uma
retentativa.

## Fluxo inclusivo

O modo `inclusive` mantém o comportamento atual:

1. o export WAS alimenta os findings normalizados;
2. o detalhe oficial do plugin complementa as classificações OWASP 2025;
3. cada finding participa de todas as categorias associadas ao plugin;
4. as linhas são ordenadas por VPR decrescente, severidade, instâncias e Plugin
   ID.

Nenhuma consulta de análise exclusiva é feita nesse modo.

## Fluxo exclusivo

Para cada categoria de `A1` a `A10`, a aplicação executa uma consulta somente de
leitura à análise de findings WAS usada pela console, agrupada por Plugin ID. O
contrato inclui:

- `state` diferente de `FIXED`;
- `severity` diferente de informativa;
- `risk_modified` diferente de `ACCEPTED`;
- `owasp_2025` exatamente igual à categoria consultada;
- `last_seen` dentro do período `[início, fim)` no fuso do cliente;
- agrupamento por `definition.id`;
- severidade máxima, nome, família, VPR e quantidade de findings;
- paginação até o fim do resultado.

Os limites locais do período serão convertidos para UTC. Se a API tratar
`between` como inclusivo nas duas pontas, o limite final enviado será o último
milissegundo anterior ao `fim`, preservando o contrato `[início, fim)`.

Depois da coleta, a aplicação ordena cada categoria por VPR decrescente e mantém
os mesmos critérios de desempate do modo inclusivo. A apresentação DOCX recebe o
dataset pronto e não executa consultas nem recalcula a classificação.

## Persistência e rastreabilidade

O dataset e o manifesto registrarão, sem credenciais ou dados sensíveis:

- modo OWASP solicitado e efetivamente utilizado;
- fonte `plugin_catalog_inclusive` ou `console_analysis_exclusive`;
- período e fuso;
- estado de cobertura;
- categorias consultadas e quantidade de grupos retornados;
- contrato sanitizado ou hash do contrato da consulta.

Os resultados exclusivos serão persistidos no snapshot do período. Regenerar um
DOCX a partir do mesmo dataset não fará nova consulta e produzirá as mesmas
tabelas. Alterar a preferência do cliente passa a valer nas coletas seguintes;
um período antigo só poderá mudar de modo mediante nova coleta direcionada desse
componente.

O histórico compacto preserva também o indicador de que o WAS foi coletado e o
snapshot exclusivo final A01–A10. O replay continua reproduzível depois da limpeza
do staging, inclusive quando a coleta WAS concluída não trouxe findings.

## Falha, publicação parcial e retentativa

O modo exclusivo não utiliza fallback silencioso para o modo inclusivo. Se uma
das dez categorias falhar, o componente WAS será considerado falho e retentável,
mesmo que o export WAS e outras categorias tenham respondido.

O relatório geral e os documentos dos demais módulos continuam sendo gerados com
o conteúdo válido disponível, sem tabelas WAS parciais e sem substituir o modo
exclusivo pelo inclusivo. O conjunto e o job assumem estado parcialmente
concluído, preservando os documentos já válidos.

O painel exibe um alerta operacional para o analista informando que o componente
WAS falhou e disponibiliza a ação **Tentar somente WAS novamente**, no mesmo
modelo já utilizado para falha isolada do Cloud. Esse aviso pertence à interface
operacional; ele não deve expor detalhes técnicos, credenciais ou mensagens
internas no documento destinado ao cliente.

A retentativa seleciona somente o componente WAS e nunca repete VM, assets, TAG
ou Cloud já concluídos. Dentro do WAS, a retomada reutiliza checkpoint, export e
chunks íntegros quando ainda forem válidos e repete apenas a análise OWASP
pendente; se esses artefatos não forem mais válidos, pode recolher novamente o
WAS, ainda sem executar os demais módulos. Nenhuma retentativa inicia scans.

Antes de iniciar a retentativa, o sistema relê o modo OWASP atualmente salvo no
perfil e o apresenta na confirmação. Se a execução original começou como
`console_exclusive`, mas o analista alterou permanentemente o cliente para
`inclusive`, a nova tentativa:

1. reutiliza os findings WAS íntegros já preservados, quando disponíveis;
2. não repete a consulta exclusiva;
3. monta localmente as tabelas inclusivas;
4. regenera somente o relatório-base, único documento atual que consome essas
   tabelas WAS/OWASP;
5. substitui a publicação incompleta da mesma linhagem, sem criar uma duplicata;
6. mantém VM, assets, TAG e Cloud inalterados.

O histórico e o manifesto registram o modo original, o modo efetivo da tentativa
de recuperação e o momento da mudança, sem reescrever a evidência da tentativa
que falhou. A mudança para `inclusive` permanece ativa nas próximas coletas até
nova edição explícita do perfil.

## Compatibilidade e limites

- Perfis sem o novo campo continuam em `inclusive`.
- Relatórios e datasets históricos não são reinterpretados automaticamente.
- A rota de análise usada pela console deverá ficar isolada em um adaptador,
  porque não está presente no catálogo público atual do WAS v2.
- Mudanças futuras no contrato dessa rota devem resultar em falha retentável do
  WAS e alerta operacional, nunca em dados vazios ou fallback silencioso.
- Nenhuma credencial, cookie ou conteúdo sensível será incluído em logs,
  manifestos ou testes.

## Validação

A implementação deverá comprovar:

- leitura, validação, criação e edição dos dois valores no perfil e no painel;
- retrocompatibilidade de perfis antigos e ausência de chamadas extras no modo
  inclusivo;
- igualdade entre o payload exclusivo e os filtros definidos neste documento;
- paginação, normalização `A1` para `A01`, contagens e ordenação por VPR;
- exclusão de classificações múltiplas quando a consulta exclusiva não as
  retornar para uma categoria individual;
- exclusão de findings informativos, corrigidos ou com risco aceito;
- falha de qualquer categoria marcando WAS como falho e o conjunto como
  parcialmente concluído, sem impedir a geração dos demais documentos;
- alerta e ação **Tentar somente WAS novamente** no painel;
- retentativa seletiva que não executa VM, assets, TAG ou Cloud e reutiliza o
  export WAS quando ainda estiver válido;
- retentativa relendo e exibindo o modo permanente atualmente salvo no perfil;
- recuperação de uma tentativa exclusiva no modo inclusivo, com regeneração sem
  duplicata e auditoria dos modos original e efetivo;
- persistência do modo, fonte e cobertura no dataset e no manifesto;
- renderização e inspeção visual de DOCX sanitizado nos dois modos.

Após a implementação, `CONTEXTO.md`, o catálogo de dados, o guia de
desenvolvimento e o guia operacional deverão refletir o comportamento vigente.
