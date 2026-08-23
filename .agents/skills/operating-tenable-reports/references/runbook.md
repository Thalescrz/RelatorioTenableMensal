# Runbook operacional

## Preparar uma instalação

Na raiz do projeto:

```powershell
.\scripts\setup.ps1
.\scripts\bootstrap_postgresql.ps1
```

O bootstrap solicita a senha administrativa do PostgreSQL de forma oculta. A
aplicação usa a credencial limitada guardada em `credentials/database.env`. Chaves
Tenable ficam em `credentials/<client_id>.env`; ambos são ignorados pelo Git.

## Iniciar e encerrar a interface

```powershell
cd C:\Codex\RelatorioTenableMensalv2
powershell.exe -ExecutionPolicy Bypass -File ".\scripts\run_web.ps1"
```

Acesse `http://127.0.0.1:8765`. Encerre com `Ctrl+C` no PowerShell que iniciou o
servidor. Se a interface parecer antiga ou uma rota não existir, confirme processos
escutando a porta e reinicie pela raiz correta antes de investigar o código.

## Cliente novo

1. Abra **Gerenciar clientes** e adicione o nome.
2. Confirme os IDs automáticos; não reutilize ID de outro cliente.
3. Informe as duas chaves e salve.
4. Defina customizado, WAS, Output e filtros de validação.
5. Execute **Testar API** no cliente.
6. Se necessário, execute o teste de todos para avaliar a carteira.

O formulário não deve devolver secrets salvos. Uma falha de teste não prova chave
inválida: diferencie autenticação, rede, permissão, rate limit e rota antiga.

## TAGs

Use **Buscar TAGs da Tenable**. Habilite separadamente:

- relatório operacional para a TAG;
- comparativo temporal dentro daquele relatório.

Uma coleta VM geral é normalizada uma vez. O recorte usa os UUIDs dos ativos da TAG.
Os dois relatórios gerais mantêm o ambiente inteiro.

## Períodos

- Automático: mês-calendário anterior completo, no primeiro dia do mês.
- Manual padrão: um mês móvel até o instante da execução.
- Manual por dias: últimos `X` dias.
- Manual explícito: intervalo escolhido pelo analista.

Internamente o fim é exclusivo. Antes de iniciar, repita o período visível para o
analista e confirme o fuso do cliente.

## Gerar e acompanhar

Use o card para um cliente ou **Gerar todos** para a fila da carteira. Acompanhe:

1. validação do perfil e espaço em disco;
2. export de ativos;
3. export VM e chunks;
4. WAS, se habilitado;
5. normalização e datasets;
6. histórico e TAGs;
7. renderização, registro e limpeza.

Para VM, registre UUID, origem (`created`, `resumed` ou equivalente), estado,
chunks persistidos e última mudança. Só `FINISHED` com chunks tratados encerra a
etapa.

## Export sem progresso

Timeout VM é temporário. Use **Cancelar export e tentar novamente** somente com
confirmação do UUID e da execução. O cancelamento automático é permitido apenas
para job criado pela execução atual que chegou ao limite sem progresso. Preserve
jobs fornecidos, preexistentes ou retomados.

Se houve chunks persistidos, a tentativa seguinte deve retomá-los. Não apague o
manifesto parcial antes de diagnosticar.

## Propriedades seletivas

O padrão é payload completo. **Validar export otimizado** cria comparação real no
tenant e só deve ser executado com autorização. Ative propriedades seletivas quando
contagens, identidades, severidades, estados, datas, Top 5 e exploração forem
equivalentes.

HTTP 400 ou contrato incompleto permite um único fallback completo. Autenticação,
rate limit e timeout permanecem visíveis.

## WAS

WAS roda separadamente e é best effort. Ausência de licença, permissão ou findings
gera aviso/mensagem de ausência e não bloqueia VM. Diferencie progresso VM de
progresso WAS; chunks VM finalizados não dizem nada sobre WAS.

## Documentos e `MAIN`

Depois da publicação:

- confirme os dois DOCX gerais esperados;
- confira documentos por TAG habilitados;
- verifique hashes/registro e disponibilidade para download;
- confirme qual geração está `MAIN`.

Automático válido vira `MAIN` por padrão quando aplicável. Após uma reexecução
melhor, promova manualmente a versão correta. Antes de excluir um `MAIN`, defina a
referência substituta ou aceite conscientemente a ausência de comparativo futuro.

## Armazenamento

DOCX e histórico compacto são duráveis. Raw, snapshots, normalizados, datasets e
imagens de montagem são staging. Sucesso validado remove staging; falha o preserva
temporariamente para retomada e diagnóstico.

Antes de limpar manualmente, confirme caminho absoluto dentro de `data`, ausência
de processo ativo, publicação dos DOCX e persistência histórica.

## Diagnóstico por CLI

Use a ajuda da versão ativa:

```powershell
.\.venv\Scripts\python.exe -m tenable_reports --help
.\.venv\Scripts\python.exe -m tenable_reports run-client --help
.\.venv\Scripts\python.exe -m tenable_reports orchestrate --help
.\.venv\Scripts\python.exe -m tenable_reports database-status --help
```

Evite copiar comandos antigos sem conferir `--help`. Comandos que chamam APIs reais
devem manter a confirmação explícita exigida pela CLI.
