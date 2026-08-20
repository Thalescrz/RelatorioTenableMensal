# Matriz comparativa e contrato dos dois relatórios

**Data da análise:** 2026-08-12  
**Amostra:** Cliente Y, Cliente X, Cliente Z e Cliente A  
**Escopo:** estrutura, conteúdo, semântica, identidade visual, recorrência e possibilidade de automação

## 1. Conclusão

Os quatro documentos pertencem claramente à mesma família editorial, mas cresceram por cópia e adição de blocos. O produto não deve cristalizar qualquer um deles como um único template universal. A saída correta é composta por dois documentos derivados do mesmo snapshot:

1. **Relatório-base:** núcleo atual e repetível do serviço, sem dependência de mês anterior.
2. **Inteligência e customizações:** catálogo unificado de análises adicionais, ativado por perfil, disponibilidade de fonte e escolha do usuário.

Essa separação reduz a deriva entre clientes, preserva uma entrega mensal previsível e permite reutilizar como produto as boas ideias que surgiram em contratos específicos.

## 2. Evidência analisada

| Modelo | Páginas | Parágrafos | Tabelas | Ênfase observada |
|---|---:|---:|---:|---|
| Cliente Y | 41 | 489 | 35 | Comparativos mensais, recortes por rede e evolução |
| Cliente X | 22 | 390 | 25 | Versão mais compacta e Container Images |
| Cliente Z | 38 | 614 | 54 | Comparação anterior extensa, Top 5 WEB, vetor de ataque e Container Images |
| Cliente A | 70 | 749 | 38 | Evolução longa, software sem suporte, Top 5 WEB e muito Plugin Output |

Total inspecionado: **171 páginas, 2.242 parágrafos e 152 tabelas**. Todos os documentos são A4 retrato, usam capa/contracapa corporativas, azul como cor dominante, cabeçalho/rodapé persistente e tabelas por severidade.

Rastreabilidade dos originais, sem expor seus caminhos:

| Modelo | SHA-256 |
|---|---|
| Cliente Y | `F38F60C2B2D90C0D623DE8297EC2CA0454D5FDDA51F14BA5697816FB81A38E87` |
| Cliente X | `98087CE68598A63C7BA157ACA732CD7DAA9104CE70601404AAAE6F214DD41566` |
| Cliente Z | `5F0DA3FFEB149833A750C49F52722C8D3D473091FC7A77ADD45DBB5691B27004` |
| Cliente A | `BC1A686BE2C52E56D4EC0E6E1E64F5BF2CF87EF0A38C4BA5928786B637F0F` |

### Regra para os campos em branco

IP, hostname, nome de pessoa, cliente e e-mail em branco são **anonimização intencional**. Eles foram catalogados como placeholders dinâmicos sensíveis; não foram tratados como ausência operacional nem reconstruídos. Na automação, a política de redação deve decidir se esses campos aparecem, são mascarados ou são suprimidos para cada audiência.

## 3. Matriz de conteúdo

Legenda: `●` presente; `H` depende de histórico; `C` depende de capacidade/licença; `—` não observado.

| Bloco semântico | Y | X | Z | A | Classificação de origem | Destino decidido |
|---|:---:|:---:|:---:|:---:|---|---|
| Capa, sumário, controle, objetivo | ● | ● | ● | ● | Padrão global | Base |
| Texto dos sensores Nessus/Agent/NNM | ● | ● | ● | ● | Padrão global | Base, estático versionado |
| Quadro VM por OS: mitigado/não mitigado/explorável/patch >30d | ● | ● | ● | ● | Padrão global | Base |
| Principais ativos vulneráveis | ● | ● | ● | ● | Padrão global | Base, com nova coluna `Exploitable` |
| Top vulnerabilidades mitigadas | ● | ● | ● | ● | Padrão global | Base |
| Top vulnerabilidades não mitigadas | ● | ● | ● | ● | Padrão global | Base |
| Top vulnerabilidades ressurgidas | ● | ● | ● | ● | Padrão global | Base |
| Top 5 VM com descrição, solução, links e hosts | ● | ● | ● | ● | Padrão global e crítico | Base |
| Plugin Output nos hosts do Top 5 VM | —/texto | — | ● | ● | Variação sensível | Coluna opcional, desligada por padrão |
| Saúde global WAS | ● | ● | ● | ● | Padrão global, C | Base quando a fonte WAS estiver disponível |
| Aplicações WAS vulneráveis | ● | ● | ● | ● | Padrão global, C | Base |
| Vulnerabilidades WAS por plugin | ● | ● | ● | ● | Padrão global, C | Base |
| OWASP Top 10 | ● 2021 | ● 2021 | ● 2025 | ● 2021/2025 | Padrão com versão divergente | Base, taxonomia configurável/versionada |
| Top 5 WEB detalhado | — | — | ● | ● | Comum; promovido por decisão | **Base** |
| Plugin Output no Top 5 WEB | — | — | — | ● | Customização sensível | Coluna opcional, desligada por padrão |
| Comparativo mensal mitigadas/não mitigadas | H | — | — | H | Customização temporal | Inteligência |
| Série/evolução mensal | H | — | — | H | Customização temporal | Inteligência |
| Comparativo temporal dos principais ativos da mesma rede | H | — | — | — | Customização temporal por tag; não filtra a base | Inteligência |
| Comparação com relatório anterior em cada matriz | — | — | H | —/parcial | Customização temporal | Inteligência |
| Integridade/autenticação de scans | — | — | H | — | Customização operacional | Inteligência |
| Vulnerabilidades por família de plugin | — | — | H | — | Customização analítica | Inteligência |
| CVSS por status e matriz CVSS × VPR | ● | ● | ● | ● | Padrão global | Base |
| Exploráveis por vetor de ataque | — | — | H | — | Customização analítica | Inteligência |
| Sistemas operacionais mais comuns | ● | — | ● | ● | Comum | Inteligência |
| Software/SO sem suporte e ativos afetados | — | — | — | ● | Customização de ciclo de vida | Inteligência |
| Análise executiva da evolução/criticidade | — | — | — | H | Customização temporal | Inteligência |
| Aplicações WEB/tecnologias sem suporte | — | — | — | ● | Customização WAS | Inteligência |
| Tenable Cloud Security: Container Images | — | ● | ● | — | Comum, C | Inteligência |

### Observações de consistência

- Os nomes e a numeração variam, mas a semântica do núcleo se repete.
- Há documentos que denominam contagem de instâncias como “vulnerabilidades”. O novo contrato deve declarar o **grão** em cada tabela: instância/finding, plugin, ativo, aplicação ou imagem.
- A versão OWASP não pode ser texto copiado do último Word. Deve ser metadado do módulo (`owasp_catalog_version`) e a associação plugin-categoria deve ser versionada.
- Há cabeçalhos ambíguos, por exemplo duas colunas “PORTA” nas tabelas de hosts. Para fidelidade editorial, o relatório-base preserva `ASSET NAME`, `IP`, `PORTA`, `PORTA` e acrescenta somente a coluna opcional `Output` quando habilitada.
- Páginas dominadas por uma única coluna `Output` comprovam a necessidade de mantê-la fora do padrão.

## 4. DOCX 1 — relatório-base

**Nome:** `01-relatorio-base-<client_id>-<periodo>.docx`  
**Objetivo:** registrar a fotografia técnica corrente, com estrutura previsível e sem exigir histórico.

O relatório-base contém **dois blocos Top 5 detalhados e independentes**:

- **Top 5 VM não mitigadas:** o item “Vulnerabilidades e suas correções e/ou contramedidas recomendadas”, acompanhado dos hosts afetados.
- **Top 5 WEB abertas:** o item “Vulnerabilidades WEB e suas correções e/ou contramedidas recomendadas”, acompanhado das aplicações/URIs afetadas.

O Top 5 VM não foi substituído pelo Top 5 WEB e nenhum dos dois foi transferido para o DOCX de inteligência/customizações.

### Conteúdo obrigatório

1. Capa e período.
2. As três tabelas originais de controle: `Preparação`, `Controle de Versionamento` e `Lista de Distribuição`, com campos pessoais sensíveis vazios quando mascarados.
3. `OBJETIVO`, com o parágrafo original e o período dinâmico.
4. `SENSOR NESSUS, NESSUS AGENT E NESSUS NETWORK MONITOR`, seus dois parágrafos originais, o Overview, as quatro explicações de coluna e a matriz por severidade.
5. `3.2. Principais Ativos Vulneráveis`, com os dois parágrafos originais e `Exploitable` à direita de `Total`.
6. `VISÃO GERAL DAS PRINCIPAIS VULNERABILIDADES`, com as tabelas de mitigadas, não mitigadas e ressurgidas.
7. `VULNERABILIDADES E SUAS CORREÇÕES E/OU CONTRAMEDIDAS RECOMENDADAS`, com Top 5 VM detalhado, solução, links e hosts.
8. `SENSOR WAS`, saúde global, aplicações, plugins, OWASP e as definições editoriais observadas nos modelos.
9. `Vulnerabilidades WEB e Suas Correções e/ou Contramedidas Recomendadas`, com Top 5 WEB quando houver dados WAS.
10. `INCREMENTANDO A SEGURANÇA E PROTEÇÃO DO AMBIENTE`, com OS, CVSS, CVSS × VPR e VPR.
11. `RESUMO DE VULNERABILIDADES`, com estado, idade e framework.
12. A contracapa com a frase original.

Na tabela de ativos, `Exploitable` é subconjunto de `Total` e fica à direita de `Total`, exatamente como nova última coluna; não entra na soma das severidades.

Se a fonte WAS não estiver licenciada ou acessível, o documento preserva os títulos, os textos e os cabeçalhos estáticos do padrão, mas não cria frases explicativas nem transforma ausência em zero.

### Regra para `Output`

O campo não pertence ao layout padrão. Ele só aparece quando o usuário o habilita explicitamente por execução ou por perfil:

```yaml
sections:
  vm_top5:
    include_output: false
  was_top5:
    include_output: false
```

Quando habilitado, o pipeline deve sanitizar caracteres de controle e segredos, aplicar limite configurável, marcar a seção como evidência sensível e não enviar o conteúdo a um tradutor externo. O conteúdo integral pode ir para um anexo técnico protegido em vez de alargar o corpo principal.

## 5. DOCX 2 — inteligência e customizações

**Nome:** `02-inteligencia-e-customizacoes-<client_id>-<periodo>.docx`  
**Objetivo:** reunir a inteligência adicional dos quatro modelos em um catálogo reutilizável. Não é um template exclusivo de um cliente.

### Catálogo inicial de módulos

| ID estável | Módulo | Fonte atual | Histórico? | Condição |
|---|---|---|:---:|---|
| `vm_monthly_volume` | Quatro vistas mensais: não mitigadas e mitigadas, cada uma por severidade e em linha; aceita escopos como Geral/Servidores | VM snapshots | Sim | Dois ou mais pontos comparáveis por vista |
| `vm_previous_period_delta` | Comparativo corrente × anterior no overview, scan, família, CVSS/VPR, estado, idade e explorabilidade | VM snapshots | Sim | Predecessor compatível |
| `vm_asset_movement` | Entrada/saída e mudança de posição dos ativos mais vulneráveis | Findings + assets | Sim | Mesmo escopo e grão |
| `vm_network_comparison` | Principais ativos da mesma rede/tag no período atual e anterior | VM snapshots + perfil | Sim | Mesmo UUID de tag e predecessor compatível; sem filtro na base |
| `scan_auth_health` | Sucesso/falha de autenticação e cobertura de scans | Scan/asset metadata | Sim | Campo validado no tenant |
| `vm_plugin_family` | Corrigidas/não mitigadas por família de plugin | VM | Opcional | — |
| `vm_os_distribution` | Distribuição de sistemas operacionais | Assets | Opcional | — |
| `vm_eol_software` | SO/software sem suporte e ativos afetados | Plugins/assets + catálogo EOL | Opcional | Fonte EOL confiável |
| `vm_cvss_vpr_matrix` | Comparativo histórico da matriz CVSS × VPR | VM snapshots | Sim | Predecessor compatível |
| `vm_exploit_vector` | Exploráveis por framework e vetor de ataque | VM/plugins | Opcional | Sinais disponíveis |
| `vm_executive_evolution` | Evolução por plugin com aumentos, reduções e itens novos, acompanhada do texto editorial observado | Módulos anteriores | Sim | Gates de qualidade aprovados |
| `was_unsupported_tech` | Aplicações/tecnologias WEB sem suporte | WAS + catálogo EOL | Opcional | Fonte validada |
| `was_previous_period_delta` | Evolução por aplicação/plugin/OWASP | WAS snapshots | Sim | Mesmo escopo WAS |
| `cloud_container_images` | Top imagens/repositórios e CVEs com correção | Tenable Cloud Security | Opcional | Licença e fonte disponíveis |

O inventário visual completo, incluindo as variações por escopo e os vinte modelos de tabela customizada, está em `docs/11-catalogo-visual-e-tabelas-customizadas.md`.

O usuário poderá ativar/desativar módulos no futuro por CLI, tela web/desktop ou perfil versionado. O código recebe uma lista de módulos e parâmetros; não contém `if client == "..."`.

Exemplo conceitual:

```yaml
profile_version: 1
client_id: "client-a3f2"
documents:
  base: true
  intelligence:
    modules:
      - vm_monthly_volume
      - vm_previous_period_delta
      - vm_eol_software
      - vm_cvss_vpr_matrix
      - cloud_container_images
```

### Comportamento quando falta histórico

O segundo DOCX ainda pode ser gerado com módulos correntes. Módulos temporais sem predecessor compatível são omitidos; nunca comparam contra zero, nunca reaproveitam o arquivo mais recente de outro escopo e nunca criam setas de tendência artificiais.

## 6. Conteúdo estático, dinâmico e sensível

| Tipo | Exemplos | Tratamento |
|---|---|---|
| Estático versionado | Objetivo, conceito de sensores, metodologia de VPR/CVSS, definições OWASP | Biblioteca editorial com versão e revisão técnica |
| Dinâmico corrente | Contagens, ativos, plugins, VPR, severidade, hosts, URI, imagens | Derivado do snapshot e reconciliado |
| Dinâmico histórico | Deltas, séries, entrada/saída, ressurgimento, narrativa de evolução | Somente snapshots compatíveis |
| Configuração do cliente | Razão social exibida, contrato, audiência, escopos, módulos | `ClientProfile` versionado; segredos fora do arquivo |
| Sensível | IP, hostname, pessoa, e-mail, URI interna, output, repositório privado | Política de exposição/mascaramento e logs higienizados |

## 7. Decisões visuais para a futura implementação

- Preservar a identidade corporativa observada, mas reconstruir componentes em um template controlado; não copiar manualmente um dos documentos.
- A4 retrato, grade fixa, estilos semânticos de títulos, tabelas com cabeçalho repetido e quebra de linha controlada.
- Usar página paisagem ou anexo quando `Output` for habilitado; não comprimir texto a ponto de ficar ilegível.
- Gráficos devem nascer de dados estruturados e ter título, período, unidade, legenda e texto alternativo.
- Sumário, numeração e referências cruzadas devem ser campos atualizáveis no Word.
- Cada documento gerado deve ser renderizado por inteiro e passar por verificação de overflow, páginas em branco acidentais, títulos órfãos e tabelas cortadas.

## 8. Gate para transformar o contrato em código

Antes do primeiro DOCX automatizado:

1. converter os módulos em schemas versionados;
2. confirmar no tenant os campos e filtros exatos;
3. aprovar o template corporativo e a política de dados sensíveis;
4. criar fixtures sanitizadas para os quatro perfis;
5. implementar primeiro coleta/snapshot, depois métricas e somente então apresentação;
6. reconciliar todas as contagens importantes contra o raw e bloquear publicação em caso de inconsistência.
