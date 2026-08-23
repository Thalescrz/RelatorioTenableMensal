# Instruções para perfis de clientes

## Separação de configuração e segredo

Perfis JSON descrevem comportamento; nunca contêm Access Key, Secret Key, senha do
banco ou token. Segredos ficam em `credentials/<client_id>.env`, ignorado pelo Git.
Arquivos em `clients/examples` usam somente dados fictícios. Perfis gerenciados
locais não devem ser publicados sem revisão.

## Identidade

`client_id` e `tenant_id` são opacos, estáveis e normalizados. Não altere um ID para
renomear a exibição: isso quebraria histórico e compatibilidade. Nome, contatos e
campos editoriais são metadados separados e podem ser sensíveis.

## Opções

- Relatório-base é obrigatório; customizado e módulos adicionais seguem o perfil.
- WAS pode ficar habilitado por padrão porque sua ausência é tolerada.
- Cloud Security não deve ser apresentado como funcional enquanto a coleta não
  existir.
- `Plugin Output` permanece desligado salvo necessidade explícita.
- Filtros de validação são opcionais e devem permanecer discretos.
- Estratégia VM segura: combinada, 1000 ativos por chunk e propriedades seletivas
  desligadas até validação no tenant.

## TAGs

Descubra TAGs pela API antes de configurá-las manualmente. Separe a lista que gera
relatórios da lista que inclui comparativo. Selecionar TAG não filtra relatórios
gerais. Categoria e valor fazem parte da identidade histórica; preserve ambos.

## Validação

Execute `validate-profile` para qualquer exemplo alterado e os testes de perfil.
Não faça uma coleta real apenas para validar sintaxe. Ao adicionar campo, atualize
parser, serialização da interface, exemplo sanitizado, documentação e regressões de
compatibilidade.
