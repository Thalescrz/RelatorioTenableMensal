---
name: operating-tenable-reports
description: Use when operating, configuring, monitoring, or troubleshooting the local Tenable monthly report application for one or more clients.
---

# Operating Tenable Reports

Use esta skill para conduzir uma operação real ou orientar um analista sem depender
de conhecimento implícito do projeto.

## Procedimento

1. Leia as instruções `AGENTS.md` aplicáveis e identifique a raiz ativa do projeto.
2. Classifique a ação: preparar ambiente, iniciar interface, configurar cliente,
   testar API, buscar TAGs, gerar, acompanhar, recuperar falha, gerenciar `MAIN` ou
   armazenamento.
3. Consulte somente as seções necessárias do [runbook](references/runbook.md).
4. Antes de qualquer ação real, confirme cliente, período, modo e impacto.
5. Prefira a interface web para a rotina; use CLI apenas para instalação,
   diagnóstico ou recuperação controlada.
6. Nunca revele secrets. Informe presença/ausência da configuração e resultado da
   conexão, não os valores.
7. Para export, acompanhe UUID, origem, estado e chunks. `total_chunks` não equivale
   a conclusão; exija `FINISHED`.
8. Cancele somente o UUID confirmado. Job reutilizado ou retomado não deve ser
   cancelado automaticamente.
9. Ao terminar, relate resultado, documentos produzidos, referência `MAIN`, alertas
   e qualquer staging retido.

## Restrições

- Não inicie servidor, coleta, cancelamento ou migração sem pedido/autorização.
- TAG nunca filtra os relatórios gerais.
- WAS é opcional; sua indisponibilidade não deve interromper VM.
- Não exclua documentos ou staging sem resolver alvos absolutos e confirmar que não
  há execução ativa.
- Não apresente Cloud Security como implementado.
