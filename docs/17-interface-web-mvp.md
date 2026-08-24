# Interface web local — MVP

## Estado atual — 2026-08-24

Além da fila e dos downloads, o painel permite editar clientes, testar uma API ou
todas, buscar e selecionar TAGs, configurar relatórios/comparativos por TAG,
acompanhar exports e cancelar com confirmação, validar propriedades seletivas,
excluir conjuntos de relatórios permanentemente e promover a referência `MAIN`. A
área administrativa analisa e aplica backfill e limpeza. WAS é opcional e não
paralisa VM. A interface continua local e não possui autenticação multiusuário.

O painel web é uma camada operacional simples sobre a carteira de clientes, a
orquestração existente e o PostgreSQL. Ele concentra a operação normal sem exigir
linha de comando e preserva as regras de período, histórico e geração dos DOCX.

## Iniciar

No PowerShell, a partir da raiz do projeto:

```powershell
.\scripts\run_web.ps1
```

O navegador abre em `http://127.0.0.1:8765`. Para usar outra porta:

```powershell
.\scripts\run_web.ps1 -Port 8877
```

A interface aceita somente conexões locais. Para encerrar, volte ao PowerShell e
pressione `Ctrl+C`.

## Recursos do MVP

- cards com estado, progresso, último período e alerta por cliente;
- download dos DOCX/PDF registrados no PostgreSQL;
- exclusão permanente de um conjunto completo, com prévia de período, documentos,
  arquivos e espaço ocupado, confirmação digitada e substituição obrigatória se a
  geração for `MAIN`;
- cadastro de cliente, perfil e credencial local;
- edição, ativação e desativação de clientes; o ID interno permanece imutável;
- geração pontual individual ou de todos os clientes;
- teste de conexão somente leitura, individual ou para todos os clientes;
- período móvel padrão, últimos `N` dias ou intervalo específico;
- fila sequencial, com uma geração por vez;
- alertas gerais, falhas por cliente e avisos isolados por TAG;
- seleção de relatórios operacionais por TAG diretamente no cliente;
- progresso incremental `TAG atual/total` durante a montagem dos documentos;
- documentos agrupados em **Geral**, **Customizado** e **Por TAG**;
- fonte histórica configurável por cliente, com opção Inventory Findings beta;
- UUID, origem, segmento, campo temporal, chunks e tempo sem progresso do export VM;
- cancelamento confirmado de export travado e retentativa segura em estratégia separada.

Novos clientes deixam **Vulnerabilidades WEB** habilitado por padrão. A coleta
WAS é opcional e de melhor esforço: ausência de licença, autorização, endpoint
ou resposta dentro do prazo gera um aviso no card, mas não interrompe a coleta
VM nem a geração dos demais itens. Uma resposta WAS válida sem findings mantém
no documento a mensagem mensal de que não foram identificadas vulnerabilidades
WEB para detalhamento.

O cadastro grava o perfil em `clients/managed/<client_id>.json`, a carteira em
`orchestration/clients.json` e as credenciais em
`credentials/<client_id>.env`. Arquivos `.env` dessa pasta são ignorados pelo
Git. A API da tela informa apenas se as chaves estão completas; seus valores
nunca são retornados ao navegador.

## Períodos históricos e recuperação de export

Em **Gerenciar clientes → Coleta VM**, a fonte **Export VM tradicional** permanece
padrão. **Inventory Findings · beta** só é considerada para período fechado sem
snapshot compacto exato. O snapshot sempre tem precedência e as execuções
automáticas do mês anterior continuam no fluxo tradicional. Para um intervalo
exato que possa exigir a fonte beta, a tela pede confirmação e o resultado fica
marcado como **HISTÓRICO RECONSTRUÍDO**.

Quando um export VM atinge o limite sem novos chunks, o alerta mostra os dados
necessários para conferência: UUID, origem `created` ou `reused`, segmento,
`last_found` ou `last_fixed`, chunks concluídos, tempo sem avanço e limite. O botão
**Cancelar export e tentar novamente** só aparece para falha elegível, confirma o
UUID, cancela o job remoto e reenfileira a execução. Se a estratégia era
`combined` e a fase foi `no_progress`, a nova tentativa usa `split`. Jobs
preexistentes/reutilizados nunca são cancelados automaticamente.

## Relatórios por TAG

Edite um cliente e localize **Relatórios por TAG**. O fluxo é:

1. ativar o recurso;
2. clicar em **Buscar TAGs da Tenable**;
3. pesquisar ou navegar pelas categorias;
4. marcar **Gerar relatório** nas TAGs desejadas;
5. marcar **Comparativo temporal** somente nas TAGs que precisam das tabelas e dos
   gráficos mensais;
6. salvar o cliente e iniciar a geração normalmente.

A busca consulta apenas a lista de TAGs usando a credencial local. Ela não inicia
scan nem coleta de findings. Uma TAG salva que não retorne na consulta permanece
visível com aviso de indisponibilidade. Desmarcar **Gerar relatório** também remove o
comparativo daquela TAG.

Na execução, a coleta VM geral ocorre uma única vez. Os relatórios por TAG são
derivados localmente e não filtram o relatório geral. Uma falha específica aparece
no alerta do cliente, mas os documentos gerais e as demais TAGs continuam sendo
publicados. No primeiro mês sem histórico compatível, o documento corrente ainda é
gerado e não inventa valores anteriores.

## Exclusão de um conjunto

Em **Relatórios gerados**, o botão **Excluir conjunto** remove o conjunto completo,
não apenas um DOCX isolado. A interface mostra a prévia do período, a quantidade de
documentos, os arquivos localizados e o espaço que será liberado. Depois exige
motivo e a digitação exata de `EXCLUIR`.

Se o conjunto for `MAIN`, é obrigatório selecionar outra geração compatível antes
de continuar. A operação também é bloqueada enquanto houver geração ativa para o
mesmo cliente. Quando autorizada, remove os documentos, o manifesto, o snapshot
compacto e os registros relacionados no PostgreSQL. Não existe restauração pela
interface; uma recuperação posterior depende de backup externo.

Arquivos são primeiro movidos para uma área de quarentena sob `data/.purge`. Se a
alteração no banco falhar, eles retornam ao local original. A quarentena só é
eliminada depois da confirmação da transação. O botão **Restaurar** pode aparecer
apenas em registros legados que já estavam marcados pela antiga exclusão lógica.

## Limites intencionais desta versão

- a fila fica em memória; reiniciar o painel limpa apenas a visualização da fila,
  não os relatórios ou históricos já persistidos;
- não há acesso remoto nem autenticação, pois o servidor escuta somente o
  endereço local;
- o progresso expõe os chunks dos exports VM/WAS e a montagem dos relatórios por
  TAG, mas a Tenable não fornece percentual interno de processamento de um chunk;
- esta versão não exclui clientes; eles podem ser desabilitados.
