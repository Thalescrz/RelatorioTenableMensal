# Template corporativo oficial

`base-v1.docx` é o shell editorial controlado e sanitizado, derivado do documento
oficial fornecido para preservar com fidelidade a capa, a identidade das páginas
internas e a contracapa. Ele é compartilhado pelos relatórios geral, customizado,
por TAG e Cloud Security. O cliente, o contrato, o período e os metadados do arquivo
de origem não permanecem no template.

Consulte [CONTEXTO.md](../../CONTEXTO.md) para o estado vigente dos relatórios e do
controle documental.

## Contrato

- página A4 retrato;
- capa e contracapa oficiais, com imagens, fontes, endereços corporativos públicos,
  QR code e identidade Tenable/ITProtect preservados;
- cabeçalho e rodapé internos oficiais, com cliente dinâmico;
- estilos semânticos de título;
- texto narrativo do corpo justificado, sem alterar o alinhamento próprio de
  títulos, listas, notas, gráficos e páginas institucionais;
- todas as células das tabelas de dados do corpo centralizadas horizontal e
  verticalmente, sem modificar as tabelas estruturais de cabeçalho e rodapé;
- sumário materializado e navegável após a capa, criado a partir de `Heading 1` a
  `Heading 3` sem depender de atualização manual no Word;
- oito seções principais do relatório-base numeradas explicitamente de `1` a `8`;
- tabela “Principais Ativos Vulneráveis” com cabeçalho repetível;
- `Exploitable` como última coluna e subconjunto de `Total`;
- `Output` ausente por padrão;
- campos dinâmicos expressos por tokens `{{...}}`.

## Perfis tipográficos por plataforma

O shell é semanticamente único, mas a tipografia é materializada pelo perfil de
runtime. Windows x86_64 usa `windows-calibri-v1`, validando primeiro Calibri e
Arial já licenciadas no host e aceitando payload opcional somente quando completo,
autorizado e compatível com os hashes aprovados. Ubuntu Server 24.04 x86_64 usa
`linux-carlito-v1`, com Carlito e Liberation Sans redistribuíveis dentro do pacote.
Fonte ausente bloqueia o preflight: o gerador
não escolhe outra família silenciosamente.

A baseline visual Windows preserva o resultado aprovado. A baseline Linux é uma
referência separada, pois a métrica de Carlito pode produzir quebras de linha e
paginação próprias. Toda mudança no shell ou no perfil exige geração dos quatro
tipos de relatório, renderização pelo LibreOffice fixado e comparação visual por
plataforma.

Na materialização com `linux-carlito-v1`, o EMF decorativo da capa é substituído
por `assets/brand-pattern-cover.png`, rasterização da mesma arte na dimensão
efetiva de exibição, com fundo branco opaco e traço azul corporativo reforçado.
Essa forma evita tanto a interpretação verde/roxa da paleta EMF pelo LibreOffice
Linux quanto a dessaturação acinzentada causada pela redução de um traço de um
pixel. A contracapa usa espaçadores de 12,5 pt antes da logomarca, 9 pt no bloco
central e 16 pt antes dos contatos. A mensagem institucional recebe uma quebra
controlada antes de `DIGITAL.`, e a logomarca usa contorno acima/abaixo para que a
frase comece inteira na linha seguinte. A seção final possui cabeçalhos e rodapés
vazios próprios nas variantes padrão, primeira e par, impedindo que os elementos
internos sejam herdados na contracapa. O pequeno texto decorativo que o LibreOffice
Linux recortava fora da página é omitido nesse perfil. A tabela institucional usa
três colunas com centros equidistantes, aproximando Portugal do mesmo ritmo visual
de Fortaleza e Belém. Essas regras mantêm mensagem, contatos e identidade em uma
única página sem alterar o perfil
`windows-calibri-v1`.

Os PNG em `assets/` foram extraídos ou rasterizados dos materiais fornecidos pelo
usuário e são usados somente para identidade visual do relatório.

## Reconstrução

```powershell
.\.venv\Scripts\python.exe tools\build_official_word_template.py `
  --source <arquivo-oficial.docx> `
  --source-client <identificador-no-arquivo> `
  --output .\templates\corporate\base-v1.docx
```

Não edite o binário manualmente. Mudanças controladas no shell devem ser feitas em
`tools/build_official_word_template.py`, seguidas de reconstrução, testes e
renderização integral. O arquivo oficial de origem permanece fora do Git.

## Uso nos relatórios

Os geradores abrem `base-v1.docx`, preservam a capa, materializam o conteúdo entre
as páginas oficiais e recolocam a contracapa como última página. Os quatro tipos
usam títulos numerados e estilos semânticos. O sumário usa `toc 1` a `toc 3` e
hiperlinks internos para os respectivos títulos, de modo que as entradas já sejam
visíveis e navegáveis em fluxos automatizados e visualizadores sem atualização de
campos do Word.

Nos relatórios Geral e Cloud, um componente compartilhado materializa as tabelas
globais de Preparação e Controle de Versionamento. A Lista de Distribuição recebe
primeiro as entidades globais e depois os destinatários adicionais do cliente; o
perfil individual não substitui o padrão administrativo.

## Referência Cloud legada

`cloud-base-v1.docx` permanece somente como referência técnica histórica do corpo
Cloud. Novas publicações usam `base-v1.docx` como shell e geram exclusivamente o
modelo completo atual. Os valores legados `base`, `expanded` e `comparison` são
normalizados para esse único modelo; não criam documentos ou visuais diferentes.

A prova reproduzível não usa API nem credencial:

```powershell
$env:PYTHONPATH = (Join-Path $PWD 'src')
.\.venv\Scripts\python.exe scripts\render_cloud_report_fixture.py `
  --output-root artifacts\cloud-prototype --qa
```

O comando gera o único DOCX Cloud padrão e os artefatos de QA correspondentes.

## Republicação controlada

`tools/refresh_official_report_documents.py` atualiza documentos já publicados sem
consultar API. O modo padrão é uma análise somente leitura; `--apply` atua apenas
nos DOCX dos conjuntos `MAIN` registrados no PostgreSQL e referenciados por
manifestos de publicação válidos, preserva o corpo técnico, gráficos e tabelas,
valida o pacote, substitui cada conjunto atomicamente e atualiza SHA-256, manifesto
e catálogo PostgreSQL. Conjuntos não-MAIN, documentos órfãos, arquivos de QA e a
área de descarte não entram no plano. Use `--period-id AAAA-MM` para restringir a
atualização a uma competência, `--justify-body-only` quando a manutenção deve
alterar somente o alinhamento narrativo e `--materialize-toc-only` quando deve
corrigir somente o sumário, sem reconstruir o shell nem iniciar o Word. A operação
não altera a seleção `MAIN`.
