# Template corporativo do relatório-base

`base-v1.docx` é um template controlado e sanitizado, reconstruído a partir dos elementos comuns observados nos relatórios de referência. Nenhum dos DOCX de cliente foi copiado integralmente.

## Contrato

- página A4 retrato;
- capa editorial com identidade Tenable/ITProtect;
- cabeçalho e rodapé próprios, sem endereços, QR code, nomes ou e-mails;
- estilos semânticos de título;
- tabela “Principais Ativos Vulneráveis” com cabeçalho repetível;
- `Exploitable` como última coluna e subconjunto de `Total`;
- `Output` ausente por padrão;
- campos dinâmicos expressos por tokens `{{...}}`.

Os PNG em `assets/` foram extraídos dos materiais fornecidos pelo usuário e são usados somente para identidade visual do relatório.

## Reconstrução

```powershell
python -m tenable_reports build-base-template `
  --assets-dir .\templates\corporate\assets `
  --output .\templates\corporate\base-v1.docx
```

Não edite o binário como fonte primária. Mudanças controladas devem ser feitas no gerador `tenable_reports.presentation.base_report_docx`, seguidas de reconstrução, testes e renderização integral.

## Uso no relatório completo

O gerador da Fase 6 está em `tenable_reports.presentation.full_base_report_docx`. Ele abre `base-v1.docx`, preserva capa, cabeçalho, rodapé, assets e geometria e materializa o conteúdo completo a partir do dataset versionado. O template de referência permanece imutável; a validação da Fase 6 confirmou o SHA-256 `42E06953133EE8AB1BE6CF33768803FEF5327FB0809F3DC70791DF794FF22CBD`.
