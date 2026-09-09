# Template corporativo do relatório-base

`base-v1.docx` é um template controlado e sanitizado, derivado do documento oficial
fornecido para preservar com fidelidade a capa, a identidade das páginas internas e
a contracapa. O cliente, o contrato, o período e os metadados do arquivo de origem
não permanecem no template.

## Contrato

- página A4 retrato;
- capa e contracapa oficiais, com imagens, fontes, endereços corporativos públicos,
  QR code e identidade Tenable/ITProtect preservados;
- cabeçalho e rodapé internos oficiais, com cliente dinâmico;
- estilos semânticos de título;
- sumário nativo do Word na página 2, atualizado a partir dos níveis de título;
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

## Uso no relatório completo

O gerador da Fase 6 está em
`tenable_reports.presentation.full_base_report_docx`. Ele abre `base-v1.docx`,
preserva a capa, separa a contracapa, materializa o conteúdo a partir do dataset e
recoloca a contracapa como última página. O sumário usa o campo Word
`TOC \\o "1-4" \\h \\z \\u`; `w:updateFields` permanece habilitado para que o Word
recalcule números de página e entradas ao abrir o documento.

## Template Cloud Security

`cloud-base-v1.docx` é o template sanitizado do relatório Tenable Cloud Security.
Ele mantém as três famílias de páginas do modelo aprovado — capa/sumário, conteúdo
e contracapa — e usa os marcadores `{{CLIENT_NAME}}`,
`{{REPORT_MONTH_YEAR}}`, `{{TABLE_OF_CONTENTS}}` e `{{CLOUD_CONTENT_START}}`.

O mesmo template e o mesmo dataset `cloud-metrics-v1` geram o **Modelo Base** e o
**Modelo Ampliado**. Durante a homologação, `scope.cloud_security.layout` pode ser
`comparison`; depois da decisão editorial deve permanecer `base` ou `expanded`.
Módulos condicionais do ampliado só aparecem quando o teste de contrato e a
população correspondente estiverem disponíveis.

A prova reproduzível não usa API nem credencial:

```powershell
$env:PYTHONPATH = (Join-Path $PWD 'src')
.\.venv\Scripts\python.exe scripts\render_cloud_report_fixture.py `
  --output-root artifacts\cloud-prototype --qa
```

O comando gera `cloud-modelo-base.docx`, `cloud-modelo-ampliado.docx`, PDFs,
contact sheets e `cloud-prototype-manifest.json`. O manifesto registra hashes,
seções, omissões, contagem de páginas e confirma que ambos os documentos vieram da
mesma fotografia Cloud sanitizada.
