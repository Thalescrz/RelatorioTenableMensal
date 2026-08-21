# Catálogo visual e de tabelas customizadas

**Data da revisão:** 2026-08-13  
**Amostra:** quatro relatórios Word de referência, identificados apenas como Y, X, Z e A  
**Escopo:** elementos adicionais destinados ao segundo DOCX (`inteligência e customizações`)

## 1. Evidência revisada

Foram revalidadas visualmente as **171 páginas** dos quatro documentos: 41 de Y, 22 de X, 38 de Z e 70 de A. A extração estrutural contabilizou **152 tabelas**, **7 gráficos nativos do Word**, 101 arquivos de imagem no pacote OOXML e outros drawings/shapes. Logos, capa, contracapa, QR codes e imagens puramente institucionais não contam como gráficos analíticos.

Campos vazios de IP, hostname, pessoa, cliente e e-mail continuam classificados como anonimização intencional. Nenhum valor preenchido desses campos foi copiado para este catálogo.

## 2. Gráficos analíticos observados

| ID | Visual observado | Variações | Módulo de destino | Situação no gerador |
|---|---|---|---|---|
| `G01` | Comparativo de vulnerabilidades não mitigadas por mês, com Crítica, Alta, Média, Baixa e Total | Geral; Servidores | `vm_monthly_volume` | Implementado com série configurável |
| `G02` | Volume mensal de vulnerabilidades não mitigadas, em linha | Geral; Servidores | `vm_monthly_volume` | Implementado com série configurável |
| `G03` | Comparativo de vulnerabilidades mitigadas por mês, com Crítica, Alta, Média, Baixa e Total | Geral; Servidores | `vm_monthly_volume` | Implementado com série configurável |
| `G04` | Volume mensal de vulnerabilidades mitigadas, em linha | Geral; Servidores | `vm_monthly_volume` | Implementado com série configurável |
| `G05` | Evolução mensal conjunta: não mitigadas, mitigadas e novas | Geral | `vm_monthly_evolution` | Implementado |
| `G06` | Comparativo mensal de vulnerabilidades novas por severidade | Geral | `vm_monthly_evolution` | Implementado quando `new_by_severity` estiver disponível |
| `G07` | Evolução de vulnerabilidades por plugin, com barras positivas/negativas e cores de prioridade | Top 100; recorte EOL/sem suporte | `vm_executive_evolution` | Suportado parcialmente; falta reproduzir eixo bidirecional e regras de cor |
| `G08` | Distribuição dos sistemas operacionais mais comuns, em rosca | Geral | `vm_os_distribution` | Catalogado; pendente de implementação |
| `G09` | Saúde global das aplicações WAS, com dois medidores | Geral | Relatório-base/WAS | Padrão do primeiro DOCX; não duplicar no segundo |
| `G10` | Estatísticas WAS exibidas como captura do painel | Geral | Relatório-base/WAS | Preservar conteúdo equivalente; não depender de screenshot manual |
| `G11` | Evidência do painel de Container Security | Geral | `cloud_container_images` | Screenshot é opcional; as tabelas estruturadas são a fonte principal |

Os quatro primeiros gráficos formam um conjunto. A ausência da distribuição por severidade não deve ser substituída por zeros: nesse caso o renderer gera somente a linha de volume cuja série esteja disponível.

`monthly_views` permite repetir o conjunto por vistas analíticas previamente definidas,
como `Geral` e `Servidores`, sem código específico por cliente. Essas vistas gerais
continuam no relatório customizado e são independentes dos relatórios por TAG.
Selecionar uma TAG não altera esses gráficos nem o relatório-base.

## 3. Tabelas customizadas observadas

| ID | Tabela/modelo | Colunas ou grão principal | Módulo de destino | Dependência |
|---|---|---|---|---|
| `T01` | Comparativo do overview com relatório anterior | Severidade × Mitigado, Não Mitigado, Explorável, Patch >30d | `vm_previous_period_delta` | Snapshot anterior compatível |
| `T02` | Integridade/autenticação de scan corrente e anterior | Status de autenticação × total | `scan_auth_health` | Metadado validado no tenant + histórico |
| `T03` | Ranking corrente de ativos da TAG | Nº, IP, Asset Name, severidades, Total, `Exploitable` | Relatório por TAG | TAG selecionada; sensíveis mascaráveis |
| `T04` | Ranking anterior da mesma TAG | Mesmas colunas e mesmo UUID de TAG de `T03` | Relatório por TAG | Predecessor temporal compatível |
| `T05` | Movimentação do ranking dentro da mesma TAG | Posição atual/anterior, entrada, permanência, aumento e redução | Relatório por TAG | Mesma TAG e identidade estável do ativo |
| `T06` | Vulnerabilidades corrigidas por família de plugin | Família de Plugin, Total | `vm_plugin_family` | Findings corrigidos do período |
| `T07` | Família de plugin corrente e anterior | Família, Total atual, Total anterior, delta | `vm_previous_period_delta` | Snapshot anterior compatível |
| `T08` | Sistemas operacionais mais comuns corrente e anterior | Família/SO, Total | `vm_os_distribution` | Inventário de ativos + histórico opcional |
| `T09` | Ativos com SO/software sem suporte | IP, Asset Name, severidades, Total | `vm_eol_software` | Catálogo EOL confiável; sensíveis mascaráveis |
| `T10` | Plugins/SO/software sem suporte | Plugin ID, Nome, Família OS, Severidade, Total | `vm_eol_software` | Catálogo EOL confiável |
| `T11` | CVSS por status corrente e anterior | Faixa CVSS × Mitigado, Não Mitigado, Explorável, Patch >30d | `vm_cvss_vpr_matrix` | Snapshot anterior compatível |
| `T12` | Matriz CVSS × VPR corrente e anterior | Faixa CVSS × faixa VPR | `vm_cvss_vpr_matrix` | Snapshot anterior compatível |
| `T13` | Totais por faixa VPR corrente e anterior | Rating VPR × total | `vm_cvss_vpr_matrix` | Snapshot anterior compatível |
| `T14` | Estado das vulnerabilidades corrente e anterior | Novo, Ativo, Corrigido, Ressurgido × severidade/explorável | `vm_previous_period_delta` | Snapshot anterior compatível |
| `T15` | Idade das vulnerabilidades corrente e anterior | Buckets de idade × severidade | `vm_previous_period_delta` | `first_found` normalizado + histórico |
| `T16` | Exploráveis por framework corrente e anterior | Framework × Total/Crítica/Alta/Média | `vm_exploit_vector` | Sinais de exploitabilidade disponíveis |
| `T17` | Exploráveis por vetor de ataque corrente e anterior | Framework × Local/Network/Adj. Network | `vm_exploit_vector` | CVSS attack vector + histórico |
| `T18` | Top imagens de container | Repository, Tag, Crítica, Alta, Média, Baixa | `cloud_container_images` | Tenable Cloud Security |
| `T19` | Findings por imagem de container | CVE, Severidade/VPR, Software, `Fixed by` | `cloud_container_images` | Tenable Cloud Security |
| `T20` | Tecnologias/aplicações WEB sem suporte | Plugin ID, Nome, Família, Severidade, Total, App., VPR | `was_unsupported_tech` | WAS + catálogo EOL |

As tabelas do Top 5 VM e WEB com a coluna opcional `Output` pertencem ao primeiro DOCX, porque são parte dos dois blocos detalhados padrão. A opção continua desligada por padrão e não deve ser confundida com uma customização do segundo documento.

## 4. Regras de comparação histórica

Uma tabela ou gráfico corrente × anterior só pode ser publicado quando coincidirem:

- cliente e tenant;
- produto e módulo;
- escopo da mesma TAG/aplicação;
- timezone e regra de fechamento;
- severidades incluídas;
- estados coletados;
- grão da métrica;
- versão da definição do indicador.

Na falta de predecessor compatível, o bloco comparativo é omitido. Não se compara contra zero, não se reutiliza o último arquivo encontrado e não se mistura relatório automático mensal com execução pontual de outro período.

As TAGs selecionadas para `T03` a `T05` nunca filtram a população geral. Se duas
TAGs forem escolhidas, o resultado correto são dois documentos e, quando autorizado,
dois comparativos temporais separados. Não existe comparação `TAG A × TAG B`.

## 5. Contrato mínimo dos dados mensais

```json
{
  "monthly_views": [
    {
      "id": "general",
      "label": "Geral",
      "history": [
        {
          "label": "Julho/2026",
          "mitigated": 0,
          "non_mitigated": 0,
          "new": 0,
          "mitigated_by_severity": {
            "critical": 0,
            "high": 0,
            "medium": 0,
            "low": 0
          },
          "non_mitigated_by_severity": {
            "critical": 0,
            "high": 0,
            "medium": 0,
            "low": 0
          },
          "new_by_severity": {
            "critical": 0,
            "high": 0,
            "medium": 0,
            "low": 0
          }
        }
      ]
    }
  ]
}
```

Os zeros acima documentam o tipo do campo, não a política de ausência. Em produção, campo indisponível deve permanecer ausente/indisponível e nunca ser materializado como zero.

## 6. Lacunas ainda abertas

1. Implementar `G07` com eixo bidirecional, itens `NEW` e regras determinísticas de cor.
2. Implementar `G08` e `T08` a partir do inventário de ativos.
3. Expandir `vm_previous_period_delta` para `T02`, `T07` e `T11` a `T17`.
4. Decidir por perfil se `G11` entra como evidência visual; tabelas `T18` e `T19` permanecem obrigatórias quando o módulo for ativado.
5. Validar no tenant as fontes de autenticação de scan, EOL, WAS e Cloud Security antes de habilitar os módulos automaticamente.
