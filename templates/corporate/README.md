# Template corporativo oficial

`base-v1.docx` é o shell editorial controlado e sanitizado, derivado do documento
oficial fornecido para preservar com fidelidade a capa, a identidade das páginas
internas e a contracapa. Ele é compartilhado pelos relatórios geral, customizado,
por TAG e Cloud Security. O cliente, o contrato, o período e os metadados do arquivo
de origem não permanecem no template.

## Contrato

- página A4 retrato;
- capa e contracapa oficiais, com imagens, fontes, endereços corporativos públicos,
  QR code e identidade Tenable/ITProtect preservados;
- cabeçalho e rodapé internos oficiais, com cliente dinâmico;
- estilos semânticos de título;
- sumário nativo do Word na página 2, atualizado a partir de `Heading 1` a
  `Heading 3`;
- oito seções principais do relatório-base numeradas explicitamente de `1` a `8`;
- tabela “Principais Ativos Vulneráveis” com cabeçalho repetível;
- `Exploitable` como última coluna e subconjunto de `Total`;
- `Output` ausente por padrão;
- campos dinâmicos expressos por tokens `{{...}}`.

Os PNG em `assets/` foram extraídos dos materiais fornecidos pelo usuário e são usados somente para identidade visual do relatório.

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
usam títulos numerados e estilos semânticos. O sumário usa o campo Word
`TOC \\o "1-3" \\h \\z`; `w:updateFields` permanece habilitado para que o Word
recalcule números de página e entradas ao abrir o documento.

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
área de descarte não entram no plano. A operação não altera a seleção `MAIN`.
