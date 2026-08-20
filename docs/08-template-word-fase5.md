# Template Word mínimo — Fase 5

**Concluída em:** 2026-08-13  
**Template:** `base-docx-v0.1`  
**Escopo:** capa, identificação do período, visão geral compacta e seção de prova “Principais Ativos Vulneráveis”

## 1. Resultado

A Fase 5 materializa o primeiro adaptador de apresentação Word. Ele recebe exclusivamente um perfil validado e um `report-dataset.json`; não acessa a API Tenable nem os raws durante a renderização.

Artefatos principais:

- `templates/corporate/base-v1.docx`: template sanitizado e reproduzível;
- `src/tenable_reports/presentation/base_report_docx.py`: construtor e renderizador;
- `tests/fixtures/report-dataset-phase5.json`: dataset inteiramente sintético;
- `analysis_artifacts/phase5-proof/01-relatorio-base-cliente-exemplo-2026-07.docx`: prova visual mascarada.

O template é uma reconstrução controlada dos elementos comuns dos quatro relatórios. O modelo compacto do Cliente X orientou proporção e hierarquia, mas nenhum documento de cliente foi adotado integralmente.

## 2. Contrato visual

- A4 retrato, margens fixas e fonte Calibri para portabilidade;
- azul `#2E59FC` como cor dominante;
- grafismo e logotipos fornecidos pelo usuário;
- capa com cliente, título, mês e intervalo efetivo;
- cabeçalho interno com marca, tipo de relatório e cliente;
- rodapé sem endereços, telefone, QR code, nome ou e-mail;
- títulos de nível 1 semânticos;
- tabela com layout fixo, cabeçalho repetível, linhas que não quebram e cores de severidade;
- texto alternativo nos três elementos gráficos do corpo.

## 3. Seção de prova

“Principais Ativos Vulneráveis” publica as colunas, nesta ordem:

1. `IP Address`;
2. `Asset Name`;
3. `Crítica`;
4. `Alta`;
5. `Média`;
6. `Baixa`;
7. `Total`;
8. `Exploitable`.

O gerador bloqueia a publicação quando `Exploitable > Total`. A coluna não entra na soma das severidades. `Output` permanece ausente e será tratado apenas nos módulos que o habilitarem explicitamente.

## 4. Política de dados sensíveis

O comportamento normal preserva os valores existentes no dataset, necessário ao relatório técnico entregue ao cliente. A opção `--mask-sensitive` deixa `IP Address` e `Asset Name` vazios e é obrigatória para a fixture e a prova pública desta fase.

Nenhum hostname, IP, nome de pessoa, cliente real ou e-mail foi reconstruído dos espaços em branco. A fixture contém somente IDs artificiais e contagens sintéticas. O documento não herda propriedades pessoais, endereços ou QR code dos DOCX de referência.

## 5. Comandos

Reconstruir o template:

```powershell
python -m tenable_reports build-base-template `
  --assets-dir .\templates\corporate\assets `
  --output .\templates\corporate\base-v1.docx
```

Gerar a prova sanitizada:

```powershell
python -m tenable_reports generate-base-docx `
  --profile .\clients\examples\client-profile.json `
  --dataset .\tests\fixtures\report-dataset-phase5.json `
  --template .\templates\corporate\base-v1.docx `
  --output .\analysis_artifacts\phase5-proof\01-relatorio-base-cliente-exemplo-2026-07.docx `
  --mask-sensitive
```

## 6. Gates executados

- 56 testes automatizados aprovados;
- abertura e exportação por Microsoft Word sem reparo;
- renderização final com duas páginas A4;
- inspeção visual de 100% das páginas em resolução integral;
- nenhum overflow, tabela cortada, título órfão ou página em branco acidental;
- inspeção via Office MCP: 2 títulos, 3 tabelas, 3 imagens e zero avisos;
- teste OOXML para tokens residuais, cabeçalho repetível, texto alternativo e metadados sanitizados;
- reconciliação dos dez ativos e da regra `Exploitable <= Total`.

O utilitário de renderização empacotado foi tentado primeiro, mas o ambiente não possui LibreOffice. A exportação foi concluída com o Microsoft Word local em modo não interativo; as páginas do PDF foram rasterizadas apenas para QA.

## 7. Limites e próximo incremento

Esta fase prova o pipeline editorial, não o conteúdo completo. A Fase 6 deve implementar, a partir do mesmo dataset:

- sumário e controle do documento;
- escopo e infraestrutura;
- métricas e gráficos correntes;
- mitigadas, não mitigadas, ressurgidas e aging;
- Top 5 VM não mitigadas com hosts, referências, descrição e correção;
- metodologia, qualidade, limitações e contracapa;
- bloco WAS como indisponível até a Fase 8, sem inventar zeros.

O Top 5 WEB padrão depende do adaptador WAS e permanece planejado para a Fase 8. Tradução fragmentada e `Output` opcional também continuam fora da Fase 5.
