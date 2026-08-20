# Interface web local — MVP

O painel web é uma camada operacional simples sobre a carteira de clientes, a
orquestração existente e o PostgreSQL. Ele não substitui os comandos do terminal
nem muda as regras de período e geração dos dois DOCX.

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
- cadastro de cliente, perfil e credencial local;
- edição, ativação e desativação de clientes; o ID interno permanece imutável;
- geração pontual individual ou de todos os clientes;
- teste de conexão somente leitura, individual ou para todos os clientes;
- período móvel padrão, últimos `N` dias ou intervalo específico;
- fila sequencial, com uma geração por vez;
- alertas gerais e da falha mais recente de cada cliente.

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

## Limites intencionais desta versão

- a fila fica em memória; reiniciar o painel limpa apenas a visualização da fila,
  não os relatórios ou históricos já persistidos;
- não há acesso remoto nem autenticação, pois o servidor escuta somente o
  endereço local;
- o progresso é por estado (`na fila`, `gerando`, `concluído`), sem percentual
  interno de cada chamada da API Tenable;
- esta versão não exclui clientes; eles podem ser desabilitados.
