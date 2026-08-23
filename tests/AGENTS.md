# Instruções para testes

## Isolamento

Testes automatizados não acessam Tenable, PostgreSQL real, navegador externo ou
servidor já existente. Use diretórios temporários, relógios controlados, transportes
falsos e repositórios em memória. Testes de integração PostgreSQL só rodam quando o
ambiente de teste foi explicitamente preparado.

## Fixtures

Fixtures em `tests/fixtures` devem ser mínimas, determinísticas e sanitizadas. Não
copie payload real sem remover chaves, tenant, UUIDs rastreáveis, IPs, hostnames,
pessoas, e-mails e evidências sensíveis. Inclua apenas os campos necessários ao
contrato exercitado.

## Qualidade dos testes

- Teste comportamento público e resultados, não detalhes internos acidentais.
- Para correção de bug, confirme a falha antes da implementação.
- Cubra limites de período e timezone com instantes explícitos.
- Reconcilie totais, severidades, estados e identidades por UUID.
- Diferencie dado zero, população ausente e falha de coleta.
- Em exports, cubra fila, processamento, chunks fora de ordem, timeout, retomada e
  cancelamento seguro.
- Em TAGs, prove que o relatório geral permanece inalterado.

## DOCX e interface

Testes estruturais de DOCX verificam conteúdo, tabelas, relacionamentos e opções;
eles não substituem renderização e inspeção visual. Testes da interface devem
confirmar status HTTP, payload sanitizado, confirmação de ações destrutivas e erro
associado ao cliente correto.

Não compare textos extensos de documentação palavra por palavra. Para artefatos de
orientação, valide estrutura, frontmatter, arquivos e links locais.
