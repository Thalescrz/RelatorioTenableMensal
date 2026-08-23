# Contrato para validação de dados

## Identidade da conferência

Registre antes de comparar:

- `client_id` e tenant esperado;
- `run_id` e tipo de execução;
- início, fim exclusivo e timezone;
- versão de métricas e hash de escopo;
- documento geral ou categoria/valor da TAG;
- referência `MAIN` usada, se houver.

Sem essa identidade, duas telas semelhantes podem representar populações diferentes.

## Populações VM

| Métrica | Estados | Data usada | Observação |
|---|---|---|---|
| Não mitigadas | `OPEN`, `REOPENED` | `last_found` | Interface costuma exibir `Last Seen` |
| Mitigadas | `FIXED` | `last_fixed` | Nunca validar com `Last Seen` |
| Novas | ativas com primeira ocorrência no período | `first_found` | Subconjunto temporal |
| Ressurgidas | `REOPENED` | `resurfaced_at` | Exige data de ressurgimento |

O intervalo é `[início, fim)` e as severidades são Critical, High, Medium e Low.
A API recebe limite inferior, mas o fim é aplicado localmente ao campo correto.

## Filtros rápidos na plataforma

### Não mitigadas

`Explore > Findings > Vulnerabilities`; estados Active, New e Resurfaced;
severidades Critical a Low; `Last Seen` no período.

### Mitigadas

`Explore > Findings > Vulnerabilities`; estado Fixed; severidades Critical a Low;
`Last Fixed` no período.

### Ressurgidas

Estado Resurfaced/Reopened e `Resurfaced Date` no período. Se o tenant não expuser
esse nome, confirme o campo retornado pela API em vez de substituir por `Last Seen`.

### Exploitable

Aplique `Exploit Available = true` dentro da população e do período da tabela.
Para o quadro por framework, confira separadamente Metasploit, Core, Canvas e demais
flags existentes; o total geral não distribui frameworks por inferência.

## Ativos e vínculo

Conte ativos distintos por UUID. IP, hostname, FQDN e nome exibido são atributos
mutáveis e podem estar vazios ou duplicados. Um finding órfão deve aparecer na
qualidade/reconciliação, não ser associado por aproximação.

Na tabela de principais ativos, confira contagens por severidade, total e
`Exploitable`. A coluna de exploração conta findings exploráveis daquele ativo, não
ativos nem CVEs distintos.

## Rankings

O Top 5 VM parte somente das não mitigadas no período. Valide nesta ordem:

1. conjunto de findings elegíveis;
2. agregação do plugin/vulnerabilidade e ativos afetados;
3. VPR;
4. severidade;
5. desempate determinístico definido pelo dataset.

O documento detalha descrição, solução, referências e hosts segundo o padrão do
projeto. `Output` só aparece quando habilitado e coletado.

WAS usa população e ranking próprios. Não misture instâncias/URIs WEB com hosts VM.

## OWASP e frameworks

OWASP depende do mapeamento presente nos findings WAS. Categorias `A1` a `A10` são
normalizadas para `A01` a `A10`. Uma categoria sem achado é zero; um quadro sem
população deve trazer mensagem explícita de ausência.

Frameworks de exploração dependem de indicadores específicos do plugin. Se o
indicador geral for verdadeiro e nenhuma flag de framework estiver presente, conte
no total explorável, mas não atribua a um framework inventado.

## TAGs

Valide primeiro que os UUIDs do ativo pertencem à categoria/valor selecionados.
Depois aplique o mesmo contrato VM localmente. Compare apenas a mesma TAG com sua
referência anterior compatível. A soma dos relatórios por TAG pode exceder o geral
quando um ativo pertence a múltiplas TAGs; isso não é duplicidade no relatório geral.

## Histórico

Confirme que a referência anterior é `MAIN`, imediatamente anterior e compatível em
cliente, tenant, modo, timezone, versão métrica e escopo. Sem ela:

- métricas do mês corrente continuam válidas;
- gráfico/tabela comparativa deve indicar ausência;
- um mês mais antigo não pode ser usado silenciosamente.

## Reconciliação em camadas

1. Manifesto: status, chunks, hashes, contagens e cobertura.
2. Normalizado: rejeições, duplicatas, órfãos, UUIDs e datas.
3. Dataset: populações, severidades, totais, rankings e avisos.
4. Histórico: competência, compatibilidade e `MAIN`.
5. DOCX: conteúdo exibido, mensagem de ausência e filtros de validação.

Localize a primeira camada onde o valor diverge. Não corrija o renderizador quando
a causa está na coleta, nem repita a coleta quando a diferença é apenas formatação.

## Causas frequentes de diferença

- fim do período tratado como inclusivo na conferência manual;
- timezone diferente;
- mitigadas filtradas por `Last Seen` em vez de `Last Fixed`;
- interface mostrando estado ou severidade não selecionados;
- export coletado até “agora” sem corte superior local;
- ativo duplicado por IP/hostname em vez de UUID;
- propriedade seletiva sem cobertura validada;
- TAG diferente ou ativo em múltiplas TAGs;
- referência histórica não compatível;
- ranking da interface diferente do ranking local.

## Evidência de validação

Produza um resumo com período, população, filtros, valor esperado, valor observado,
diferença, camada causal e decisão. Substitua dados reais por identificadores
fictícios ou hashes antes de registrar a evidência no repositório.
