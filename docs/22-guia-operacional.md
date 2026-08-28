# Guia operacional

## Pré-requisitos

- Windows com PowerShell;
- Python 3.11 ou superior;
- PostgreSQL local acessível;
- LibreOffice para inspeção visual automatizada dos DOCX;
- chaves Tenable VM/WAS individuais para cada cliente;
- token de conta de serviço Cloud quando esse produto estiver habilitado.

Não é necessário usar a linha de comando para a rotina diária. Os comandos abaixo
servem para instalação e recuperação administrativa.

## Instalação inicial

Na raiz do projeto:

```powershell
.\scripts\setup.ps1
.\scripts\bootstrap_postgresql.ps1
```

O segundo comando solicita a senha administrativa do PostgreSQL de forma oculta,
cria ou atualiza o banco e grava somente a credencial limitada da aplicação no
arquivo local apropriado. Use
[credentials/database.env.example](../credentials/database.env.example) como
referência de nomes, sem colocar a senha real no arquivo de exemplo.

## Iniciar a interface

```powershell
cd C:\Codex\RelatorioTenableMensalv2
powershell.exe -ExecutionPolicy Bypass -File ".\scripts\run_web.ps1"
```

O painel local fica em `http://127.0.0.1:8765`. Se uma alteração recente não
aparecer, encerre todas as instâncias antigas e inicie novamente pela raiz correta.

## Cadastrar um cliente

1. Abra **Gerenciar clientes** e escolha **Adicionar cliente**.
2. Informe o nome; IDs técnicos são gerados automaticamente.
3. Salve Access Key e Secret Key no formulário local.
4. Mantenha **Vulnerabilidades WEB** habilitado quando desejar detecção automática;
   ausência de WAS não interrompe VM.
5. Habilite **Cloud Security** somente quando o cliente possuir o produto e informe
   o token próprio no campo Cloud.
6. Habilite relatório customizado, filtros de validação e coluna `Output` conforme
   a necessidade do cliente.
7. Salve e use **Testar API** e, quando aplicável, **Testar API Cloud** no próprio
   cliente.

As chaves ficam no arquivo local ignorado pelo Git e não retornam para a tela.

## Cloud Security

1. Em **Gerenciar clientes**, habilite **Cloud Security**.
2. Informe o token da conta de serviço. Ele é salvo como `TCS_API_SECRET` no arquivo
   local `credentials/<client_id>.env`; um campo vazio em edição preserva o token
   existente.
3. Escolha o ambiente GraphQL correspondente ao tenant.
4. Clique em **Testar API Cloud** antes da primeira coleta. O teste valida somente
   credencial e contrato mínimo; ele não gera o relatório completo. A interface não
   possui seletor de modelo: toda execução Cloud habilitada gera o único documento
   padrão completo.

Quando habilitado, Cloud começa junto com VM, WAS, customizado e TAG, mas possui
progresso e falha próprios. A coleta representa o estado no instante da execução.
Solicitar um período passado não reconstrói o fechamento histórico sem uma
fotografia Cloud compatível já preservada.

As colunas `Fixed by` dependem de uma fonte GraphQL opcional do tenant. Quando a
fonte não existir, não houver permissão ou a vulnerabilidade não informar versão, o
relatório usa `N/D` e continua. Isso não deve ser tratado como falha da coleta Cloud
obrigatória nem preenchido manualmente por inferência de texto.

Uma fotografia exata pode ser reutilizada. Outra coleta Cloud completa dentro de
24 horas é bloqueada por padrão para evitar consumo repetido; atualização forçada
exige ação manual explícita. Se Cloud falhar, os demais documentos permanecem
válidos e o histórico oferece **Tentar Cloud novamente**, que não repete VM, WAS,
customizado ou TAG.

## TAGs e relatórios por TAG

1. No perfil do cliente, clique em **Buscar TAGs da Tenable**.
2. Habilite **Relatórios por TAG** somente para clientes que precisam deles.
3. Marque **Gerar relatório** nas TAGs desejadas.
4. Marque **Comparativo temporal** apenas nas TAGs que precisam dessa análise.

Uma única coleta geral atende os relatórios gerais e os recortes por TAG. Escolher
uma TAG não reduz o universo dos relatórios gerais.

## Gerar relatórios

### Cliente individual

No card do cliente, escolha gerar relatório, confirme o modo manual e defina:

- padrão: um mês móvel até agora;
- últimos dias: janela de `X` dias;
- período específico: datas inicial e final escolhidas.

No período específico, as duas datas são inclusivas para o analista. A aplicação
transforma a data final no início do dia seguinte: selecionar 01/07 a 31/07 produz
o intervalo técnico `[01/07 00:00, 01/08 00:00)`. Assim, nenhum minuto do último
dia é perdido.

Quando existir um snapshot compacto exato, a execução padrão o reutiliza e não
abre novos exports. Para testar a integração ou atualizar deliberadamente a coleta,
marque **Forçar nova coleta pela API**. A opção vale somente para aquela execução,
preserva o snapshot anterior e fica visível no progresso do card. Em períodos já
encerrados, a nova coleta é uma reconstrução histórica e pode divergir do estado
observado no fechamento original. O fluxo automático mensal não força a coleta
quando já existe um snapshot exato.

**Forçar nova coleta** e **Propriedades seletivas** são controles independentes.
O primeiro decide entre replay e novos jobs de API; o segundo altera o payload do
export VM somente quando habilitado e validado para o tenant. Portanto, é possível
testar uma coleta real nova mantendo o payload completo e a rota VM tradicional.

Se Cloud estiver habilitado, a mesma execução também inicia o componente GraphQL e
publica as variantes selecionadas. Isso não altera o período nem o universo VM.

### Evidência autenticada de referência

Em 24/08/2026, uma execução manual sanitizada para período mensal encerrado foi
concluída em uma tentativa, sem replay, com os novos exports VM e WAS em
`FINISHED` e todos os chunks processados. Foram publicados os dois DOCX gerais e
um DOCX por TAG habilitada. A limpeza pós-publicação removeu o staging pesado e
preservou o snapshot compacto.

A rota usada nessa validação foi `legacy_vm`, com propriedades seletivas
desativadas. Como essa origem aplica `since` como limite inferior, mas não oferece
limite superior, a aplicação delimitou o fim do período localmente e registrou
`HISTORICAL_RECONSTRUCTION` com aviso. A duração observada foi de aproximadamente
seis minutos; ela é apenas evidência operacional, não um SLA para outros tenants.

### Carteira

Use **Gerar todos** para colocar todos os clientes habilitados na fila. A execução
sequencial é a configuração segura inicial porque reduz concorrência de exports e
torna os limites da API mais previsíveis.

### Automático mensal

A tarefa agendada chama o fluxo automático no primeiro dia do mês e coleta o mês
anterior completo. O script de instalação é `scripts/install_monthly_task.ps1`; o
fluxo executado fica em `scripts/run_monthly_orchestration.ps1`.

## Acompanhar progresso

O card informa etapa, cliente, execução e alertas. Durante VM, observe UUID do
export, origem, status e chunks persistidos. Durante WAS e Cloud, a interface deve
indicar etapas e falhas independentes; o progresso Cloud mostra contrato, fontes,
normalização, fotografia e renderização das variantes.

Estados importantes:

- em fila: a Tenable ainda não iniciou o processamento;
- processando: export ativo, com ou sem chunks disponíveis;
- finalizado: estado remoto `FINISHED` e chunks tratados;
- falha temporária: pode receber nova tentativa;
- falha permanente: requer correção de perfil, credencial ou contrato.

Um export com `total_chunks=1` ainda pode estar processando. Não considere o número
de chunks como confirmação de término.

Para uma execução com coleta nova, confirme também:

- indicação de coleta forçada no card;
- origem `created` em vez de replay;
- VM e WAS independentes, cada um em `FINISHED` quando disponível;
- Cloud independente, com fotografia e documento padrão publicado quando habilitado;
- quantidade de chunks concluídos igual à quantidade total;
- rota e estado de reconstrução registrados no resultado;
- DOCX esperados disponíveis e limpeza concluída após a publicação.

## Export preso e cancelamento

Quando um job fica sem progresso, a interface oferece **Cancelar export e tentar
novamente** com confirmação e UUID. Use essa ação apenas depois de confirmar que o
job pertence à execução apresentada.

A aplicação cancela automaticamente somente exports criados pela execução atual
que chegam ao limite sem qualquer progresso. Um export reutilizado ou retomado não
é cancelado automaticamente para evitar destruir trabalho válido de outra execução.

## Propriedades seletivas

O padrão seguro é payload completo. A opção seletiva deve ser ativada por cliente
somente após **Validar export otimizado** comprovar equivalência de contagens,
identidades, datas, Top 5 e indicadores de exploração no tenant.

Se a API rejeitar `properties` com HTTP 400 ou retornar contrato incompleto, ocorre
um único fallback para o payload completo. A validação real cria exports e exige
confirmação consciente do analista.

## Relatórios publicados e `MAIN`

Na lista de documentos do cliente é possível:

- baixar o DOCX;
- excluir permanentemente o conjunto completo com confirmação;
- promover uma geração como `MAIN` para o próximo comparativo.

Para excluir um conjunto:

1. clique em **Excluir conjunto**;
2. confira período, quantidade de documentos, arquivos e espaço ocupado;
3. informe o motivo e digite exatamente `EXCLUIR`;
4. se a geração for `MAIN`, escolha uma substituta compatível dentre as opções
   apresentadas.

A exclusão não prossegue se existir geração ativa para o cliente ou se algum alvo
estiver fora da raiz `data`. Quando concluída, remove do disco os DOCX gerais,
customizados, por TAG e Cloud, o manifesto e os demais arquivos registrados; também
remove snapshots compactos VM e Cloud, publicação, documentos, artefatos, eventos e
execução associados
no PostgreSQL. Não há botão de desfazer. Registros legados anteriormente excluídos
de forma lógica ainda podem mostrar **Restaurar**, mas esse não é o fluxo das novas
exclusões.

Uma execução automática bem-sucedida torna-se `MAIN` por padrão. Se o analista
refizer o relatório por falta de dados, deve promover manualmente a melhor versão.
O sistema nunca escolhe a base apenas pelo nome do arquivo.

O backfill de relatórios antigos está disponível na área administrativa. A análise
é exibida antes da aplicação; arquivos ambíguos não devem ser associados sem
revisão.

## Armazenamento

Os documentos publicados e o histórico compacto permanecem. Staging pesado é
removido depois do sucesso. Em falhas, ele fica temporariamente disponível para
diagnóstico e retomada e depois entra na limpeza.
Quando Cloud falha com checkpoint reutilizável, seu staging permanece protegido até
a retentativa ou a janela de retenção aplicável.

Documentos e histórico compacto deixam de permanecer quando o próprio conjunto é
excluído explicitamente pela interface.

Não copie `data`, `credentials` ou arquivos `.env` para o Git. Antes de uma limpeza
manual, confirme que nenhum processo está em execução e que os DOCX registrados e
o histórico compacto estão preservados.

## Diagnóstico rápido

| Sintoma | Verificação |
|---|---|
| Todas as APIs falham | servidor antigo, arquivo de credenciais, relógio e acesso ao tenant |
| Rota não encontrada | reinicie o servidor usando a versão atual do projeto |
| Export VM demora | status remoto, fila, chunks, progresso e limites configurados |
| WAS não aparece | licença/permissão, capacidade habilitada e eventos específicos do WAS |
| Cloud não aparece | opção habilitada, `TCS_API_SECRET`, ambiente e resultado de **Testar API Cloud** |
| Cloud falhou sozinho | consulte o alerta do componente e use **Tentar Cloud novamente** |
| Customizado sem comparação | existência e compatibilidade da referência `MAIN` anterior |
| Documento por TAG vazio | TAG atual, UUIDs associados e período do dataset |
| Disco cresce | execuções falhas retidas e política de limpeza de staging |

Logs podem ser usados para diagnóstico, mas nunca devem ser compartilhados sem
revisão de dados sensíveis.

## Operação por linha de comando

Para intervenção controlada, consulte a ajuda atual:

```powershell
.\.venv\Scripts\python.exe -m tenable_reports --help
.\.venv\Scripts\python.exe -m tenable_reports run-client --help
.\.venv\Scripts\python.exe -m tenable_reports orchestrate --help
.\.venv\Scripts\python.exe -m tenable_reports retry-cloud --help
```

Não execute coleta real ou cancelamento fora da interface sem identificar cliente,
período, UUID e impacto.
