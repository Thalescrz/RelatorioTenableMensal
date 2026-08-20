# Coleta Efêmera e Histórico Compacto — Especificação de Design

**Data:** 20/08/2026  
**Status:** desenho aprovado em conversa; aguardando revisão deste documento  
**Escopo:** coleta Tenable VM/WAS, normalização, histórico mensal, retenção e interface web

## 1. Contexto

O projeto ocupa atualmente aproximadamente 14,28 GB. Desse total, 12,85 GB
estão em `data/` e 1,28 GB em `analysis_artifacts/`. Os arquivos `.jsonl`
representam 13,86 GB. Somente duas execuções manuais do cliente `trt15`
ocupam 7,85 GB em respostas brutas.

A retenção existente preserva dados brutos bem-sucedidos por 60 dias e dados
normalizados por 90 dias. Essa política protege a recuperação, mas permite
acumular várias coletas completas antes da limpeza. Em uma amostra real de
64 MB, gzip reduziu os dados brutos em 85,7% e os normalizados em 82%.

Relatórios passados não precisam ser regenerados a partir das respostas brutas.
Depois de uma publicação válida, somente os DOCX e o histórico compacto
necessário para comparações futuras devem permanecer.

## 2. Objetivos

- Impedir crescimento permanente de aproximadamente 10 GB por competência.
- Tratar respostas brutas, dados normalizados e datasets de apresentação como
  artefatos temporários.
- Preservar indefinidamente os DOCX e o histórico compacto de cada relatório.
- Manter comparações mensais, evolução, findings novos, mitigados e ressurgidos,
  comparativos por tag/rede e seleção manual de relatório `main`.
- Nunca remover dados temporários antes de confirmar documentos válidos e
  histórico persistido no PostgreSQL.
- Permitir retentativa e diagnóstico de execuções incompletas durante sete dias.
- Exibir consumo e reciclagem pela interface web, sem exigir linha de comando.

## 3. Não objetivos

- Manter um arquivo histórico completo de todas as respostas da API Tenable.
- Reproduzir um relatório antigo a partir dos dados brutos locais.
- Transformar o PostgreSQL em um data lake contendo cada resposta integral da API.
- Alterar as regras de cálculo, o conteúdo textual ou o layout dos relatórios.
- Apagar imediatamente os 14,28 GB existentes sem inventário, confirmação do
  histórico e prévia do que será removido.

## 4. Alternativas avaliadas

### 4.1. Coleta efêmera com histórico compacto — escolhida

As respostas são gravadas comprimidas, processadas em fluxo e descartadas após
a publicação válida. O PostgreSQL recebe somente o estado compacto necessário
para comparações. Essa opção reduz o disco sem perder inteligência histórica.

### 4.2. Persistir todos os findings no PostgreSQL

Facilitaria consultas analíticas livres, mas apenas moveria o crescimento do
sistema de arquivos para o banco. Exigiria particionamento, manutenção e uma
política de expurgo muito mais complexa. Não atende à necessidade atual.

### 4.3. Arquivar respostas em armazenamento externo

Preservaria a capacidade de reprodução completa, mas criaria custo e operação de
object storage. Foi rejeitada porque a reprodução histórica a partir do bruto
não é requisito.

## 5. Arquitetura escolhida

```text
API Tenable VM/WAS
        |
        v
coleta comprimida em staging por run_id
        |
        v
normalização em fluxo + métricas + dataset temporário
        |
        v
geração e validação dos DOCX
        |
        v
transação PostgreSQL:
snapshot compacto + registro do relatório + referência main
        |
        v
marcação da execução como publicada
        |
        v
remoção imediata do staging pesado
```

Cada execução continua imutável e identificada por `run_id`. A coleta pode ser
automática ou manual, mas usa o mesmo protocolo de publicação e reciclagem.

## 6. Ciclo de vida dos dados

| Categoria | Execução bem-sucedida | Execução com falha | Destino permanente |
|---|---|---|---|
| Respostas brutas VM/WAS | remover após publicação confirmada | manter comprimidas por 7 dias | nenhum |
| Dados normalizados | remover após publicação confirmada | manter comprimidos por 7 dias quando úteis à retentativa | nenhum |
| Dataset de apresentação | remover após publicação confirmada | manter por 7 dias para diagnóstico | nenhum |
| Renderizações e temporários do Office | remover ao final da validação | remover quando não forem necessários ao diagnóstico | nenhum |
| Manifestos de coleta | persistir resumo no PostgreSQL e remover arquivo local | manter por 7 dias | resumo PostgreSQL |
| Relatório-base DOCX | manter | não aplicável | sistema de arquivos + registro PostgreSQL |
| Relatório customizado DOCX | manter quando gerado | não aplicável | sistema de arquivos + registro PostgreSQL |
| Snapshot histórico compacto | manter | não publicar | PostgreSQL |
| Logs operacionais sanitizados | manter por 90 dias | manter por 90 dias | PostgreSQL ou arquivos pequenos |

A exclusão dos DOCX continua sendo uma ação explícita do analista. A exclusão
lógica de um relatório não remove automaticamente o snapshot usado por outras
comparações ou eventos de auditoria.

## 7. Formato da coleta temporária

- Cada página/chunk da API deve ser gravada diretamente como `.jsonl.gz`.
- A coleta não deve criar primeiro uma cópia `.jsonl` descompactada.
- Leitores de normalização devem aceitar `.jsonl` legado e `.jsonl.gz` novo.
- A normalização deve ler registro por registro e evitar carregar um arquivo
  completo em memória.
- Quando for necessário materializar dados normalizados, o formato também será
  `.jsonl.gz` e permanecerá no staging da execução.
- O manifesto registra tamanho comprimido, tamanho lógico, quantidade de
  registros, checksum, status e chunks concluídos para permitir retomada.

O staging permanece dentro da raiz controlada da execução e nunca utiliza uma
pasta ampla ou configurada de forma implícita.

## 8. Histórico compacto no PostgreSQL

O snapshot histórico deve conter somente informações utilizadas por relatórios
ou comparações:

- identificação do cliente, tenant, competência, período, escopo e versão das
  métricas;
- totais mensais por estado e severidade;
- métricas de explorabilidade, patch, CVSS, VPR, sistemas operacionais e WAS;
- contagens por Plugin ID necessárias às evoluções e rankings;
- resumos por tag/rede necessários ao comparativo da mesma rede entre períodos;
- proveniência dos filtros e versões das regras de cálculo;
- referência aos DOCX, checksums, tamanho, status e indicador `main`;
- conjuntos compactos de findings necessários para calcular entradas, saídas e
  ressurgimentos entre dois snapshots.

### 8.1. Identidades compactas de findings

A chave canônica de finding já utilizada pelo domínio será transformada em um
fingerprint SHA-256 truncado para 128 bits. Os fingerprints serão ordenados,
concatenados e comprimidos antes de serem armazenados em colunas `bytea`.

Cada snapshot terá conjuntos separados para findings abertos, corrigidos e
ressurgidos. A versão do algoritmo fará parte do snapshot. A comparação só será
permitida entre snapshots com a mesma versão. O formato reduz espaço, não expõe
hostname/IP diretamente e mantém probabilidade de colisão desprezível para o
volume esperado.

### 8.2. Estrutura relacional

Os campos consultados para localizar o predecessor permanecem relacionais e
tipados: `client_id`, `tenant_id`, `execution_type`, `period_mode`, `timezone`,
`metric_definition_version`, `scope_hash`, `period_start_at`, `period_end_at` e
`run_id`. Datas usam `timestamptz`, indicadores usam `boolean` e tamanhos usam
`bigint`.

Resumos variáveis ficam em `jsonb`; conjuntos de fingerprints ficam em `bytea`.
Uma restrição única por `run_id` preserva idempotência. O índice principal de
busca histórica começa pelas colunas de igualdade do cliente/escopo e termina
pela data do período. Não será adotado particionamento inicialmente, pois o
snapshot compacto não deve atingir cem milhões de registros; essa decisão será
reavaliada por métricas reais.

## 9. Protocolo seguro de publicação

Uma execução bem-sucedida deve cumprir, nesta ordem:

1. finalizar e reconciliar as coletas VM/WAS solicitadas;
2. construir as métricas e o dataset temporário;
3. gerar os documentos aplicáveis;
4. validar o pacote DOCX, presença das seções e arquivos esperados;
5. criar o snapshot histórico compacto;
6. em uma transação PostgreSQL, registrar snapshot, documentos e referência;
7. promover automaticamente o primeiro relatório elegível a `main`, quando
   aplicável;
8. confirmar que snapshot e documentos registrados podem ser relidos;
9. marcar a execução como `PUBLISHED_AND_COMPACTED`;
10. criar um plano de limpeza limitado ao `run_id` recém-publicado;
11. remover `raw`, `normalized`, `report-datasets` e temporários;
12. registrar bytes removidos e eventuais resíduos.

Se qualquer passo de 1 a 8 falhar, nenhuma limpeza pesada será executada. Se a
limpeza falhar depois da publicação, o relatório permanece válido e a execução
fica com estado `CLEANUP_PENDING`, permitindo nova tentativa pela interface.

## 10. Política de falhas e retentativas

- Execução ativa: nunca elegível para limpeza.
- Falha retomável: staging comprimido protegido por sete dias.
- Falha não retomável: staging protegido por sete dias para diagnóstico.
- Nova tentativa da mesma tarefa lógica pode reutilizar chunks íntegros pelo
  manifesto, mas recebe seu próprio registro de tentativa.
- Após sete dias, resíduos de falhas podem ser removidos automaticamente.
- Um snapshot nunca é publicado a partir de uma execução incompleta.
- O processo deve registrar razões de proteção e de remoção sem registrar
  credenciais ou conteúdo dos findings.

## 11. Controle de disco

O controle ocorrerá antes, durante e depois da execução:

- **Antes:** estimar o pico usando o histórico do cliente, verificar espaço
  livre e executar a limpeza de resíduos já elegíveis.
- **Durante:** acompanhar bytes comprimidos e interromper de forma controlada se
  a margem de segurança for ameaçada.
- **Depois:** eliminar o staging da execução publicada e medir o espaço liberado.

O cálculo deve considerar dados comprimidos, não multiplicar indefinidamente o
tamanho da última coleta descompactada. A margem mínima de segurança permanece
configurável e nunca pode ser inferior a 10 GB sem alteração explícita.

## 12. Interface web

A interface deve oferecer uma área simples de armazenamento com:

- espaço livre no disco que contém a raiz de dados;
- espaço temporário total e por cliente;
- quantidade de execuções com limpeza pendente;
- data e resultado da última reciclagem;
- estimativa de espaço necessário antes de iniciar uma geração;
- botão `Limpar dados temporários`;
- prévia dos itens que serão removidos e dos itens protegidos;
- resultado com bytes liberados e erros individualizados;
- estado de limpeza no acompanhamento de cada geração.

A limpeza manual usa as mesmas proteções da limpeza automática. A interface não
oferece um modo de ignorar snapshot ausente, execução ativa ou retentativa
pendente.

## 13. Migração dos dados existentes

Os aproximadamente 14,28 GB atuais serão tratados por um fluxo controlado:

1. inventariar cada `run_id` e seus documentos;
2. localizar ou reconstruir o dataset compacto somente quando necessário;
3. confirmar o snapshot no PostgreSQL;
4. associar o snapshot ao relatório registrado e ao `main` correspondente;
5. apresentar na interface a prévia de limpeza e o espaço estimado;
6. remover dados pesados apenas após confirmação do analista;
7. registrar auditoria com caminhos, categorias, tamanhos e resultado.

Artefatos de desenvolvimento em `analysis_artifacts/` não participam do
histórico operacional. Depois de uma prévia separada, podem ser removidos como
resíduos de QA sem interferir nos relatórios dos clientes.

## 14. Compatibilidade

- Arquivos `.jsonl` existentes continuam legíveis durante a migração.
- Novas coletas usam `.jsonl.gz` por padrão.
- Snapshots históricos atuais serão migrados de forma idempotente para o formato
  compacto versionado.
- O relatório `main` continua selecionável manualmente.
- Relatórios já gerados continuam acessíveis e não precisam de seu dataset bruto.
- CSV histórico permanece como formato opcional de exportação de resumos, sem
  tentar representar fingerprints ou dados brutos.

## 15. Testes e verificações

- Testes unitários de escrita/leitura gzip e compatibilidade com JSONL legado.
- Testes de streaming para confirmar uso limitado de memória.
- Testes de fingerprints determinísticos, versionados e comparáveis.
- Testes de migração PostgreSQL idempotente e índices de predecessor.
- Testes transacionais: falha antes da confirmação nunca limpa dados.
- Testes de retenção: sucesso limpa imediatamente; falha preserva por sete dias.
- Testes de proteção para execução ativa, retentativa e snapshot ausente.
- Teste ponta a ponta de dois meses confirmando comparativos idênticos antes e
  depois da compactação.
- Testes da interface para prévia, aplicação e relatório de espaço liberado.
- Execução da suíte completa e geração dos dois DOCX de validação.

## 16. Critérios de sucesso

- Uma execução publicada não deixa diretórios pesados de `raw`, `normalized` ou
  `report-datasets` associados ao seu `run_id`.
- O histórico compacto permite gerar os mesmos números de comparação obtidos
  antes da mudança.
- A coleta comprimida reduz em pelo menos 75% o espaço temporário em relação ao
  JSONL descompactado; valores inferiores geram alerta, não exclusão insegura.
- O crescimento permanente mensal fica limitado aos DOCX, registros operacionais
  pequenos e snapshot compacto.
- Nenhum arquivo é removido quando documento ou snapshot não está confirmado.
- Toda limpeza informa quantos bytes foram removidos, protegidos ou ficaram
  pendentes.
- O analista consegue operar coleta, acompanhamento e limpeza somente pela
  interface web.

## 17. Sequência de entrega

1. Suporte a coleta e leitura `.jsonl.gz` com compatibilidade legada.
2. Snapshot PostgreSQL compacto e migração idempotente.
3. publicação transacional e limpeza imediata pós-sucesso;
4. controle de disco baseado em tamanhos comprimidos;
5. painel e ações de armazenamento na interface web;
6. migração assistida e limpeza dos dados existentes;
7. validação completa com dois períodos consecutivos e relatório `main`.

