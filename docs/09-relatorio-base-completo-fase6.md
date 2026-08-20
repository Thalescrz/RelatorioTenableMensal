# Relatório-base fiel e documento de customizações

**Revisado em:** 2026-08-13  
**Versão do relatório-base:** `base-fiel-v2.0`  
**Entrada:** perfil validado + `report-dataset.json`

## Resultado

O renderizador anterior foi substituído porque adicionava parágrafos, cartões e tabelas que não existiam nos quatro documentos de referência. A versão atual separa a entrega em:

1. `01-relatorio-base-<cliente>-<periodo>.docx`: núcleo editorial comum, preservando as frases, títulos e esquemas de tabela dos modelos;
2. `02-inteligencia-e-customizacoes-<cliente>-<periodo>.docx`: somente módulos adicionais habilitados no perfil e respaldados por dados.

O relatório-base contém os dois Top 5 independentes: o Top 5 VM não mitigado e o Top 5 WEB. A tabela de ativos termina em `Exploitable`. A coluna `Output` continua desligada por padrão e só é acrescentada aos hosts dos Top 5 por configuração explícita.

## Fidelidade editorial

- os textos estáticos estão isolados em `editorial_catalog.py` e foram transcritos dos DOCX de referência;
- datas numéricas e datas por extenso são calculadas a partir do intervalo efetivo e a expressão do período permanece em negrito;
- descrições técnicas longas são divididas em blocos sem truncamento;
- o ponto de integração de tradução recebe esses blocos em ordem, permitindo traduzir descrições e soluções extensas parte a parte sem enviar `Plugin Output`;
- campos de pessoa, e-mail, IP, hostname, URI ou repositório podem permanecer vazios por `--mask-sensitive`;
- não existem os blocos criados na iteração anterior, como “Metodologia, qualidade e limitações”, cartões de KPI ou “Relatório-base concluído”;
- tabelas sem dados conservam apenas o cabeçalho original; nenhuma frase artificial de indisponibilidade é adicionada.

## Comando do par de documentos

```powershell
python -m tenable_reports generate-report-pair `
  --profile .\clients\examples\client-profile-all-customizations.json `
  --dataset .\tests\fixtures\report-dataset-phase5.json `
  --template .\templates\corporate\base-v1.docx `
  --base-output .\analysis_artifacts\phase6-fidelity\01-relatorio-base-cliente-exemplo-2026-07.docx `
  --custom-output .\analysis_artifacts\phase6-fidelity\02-inteligencia-e-customizacoes-cliente-exemplo-2026-07.docx `
  --mask-sensitive
```

## Módulos de prova no segundo DOCX

O perfil sanitizado de demonstração habilita textos e estruturas observados nos modelos: comparativo mensal, comparação com período anterior, comparativo temporal da mesma rede, integridade da varredura, família de plugin, software/SO sem suporte, análise executiva, evolução mensal, Container Images, vetor de ataque e tecnologias WEB sem suporte. As tags usadas nesse comparativo não filtram o primeiro DOCX. Em produção, cada módulo depende de dados próprios e, quando temporal, de um predecessor compatível do mesmo cliente e escopo.
