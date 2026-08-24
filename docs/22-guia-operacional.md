# Guia operacional

## Pré-requisitos

- Windows com PowerShell;
- Python 3.11 ou superior;
- PostgreSQL local acessível;
- LibreOffice para inspeção visual automatizada dos DOCX;
- chaves Tenable individuais para cada cliente.

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
5. Habilite relatório customizado, filtros de validação e coluna `Output` conforme
   a necessidade do cliente.
6. Salve e use **Testar API** no próprio cliente.

As chaves ficam no arquivo local ignorado pelo Git e não retornam para a tela.

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
export, origem, status e chunks persistidos. Durante WAS, a interface deve indicar
que esse fluxo é independente.

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
- excluir um relatório com confirmação;
- promover uma geração como `MAIN` para o próximo comparativo.

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
```

Não execute coleta real ou cancelamento fora da interface sem identificar cliente,
período, UUID e impacto.
