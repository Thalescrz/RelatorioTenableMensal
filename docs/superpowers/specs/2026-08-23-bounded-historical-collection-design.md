# Design: coleta histórica delimitada e resiliência dos exports Tenable VM

**Data:** 2026-08-23  
**Status:** aprovado em conversa; pronto para implementação  
**Escopo:** coleta Tenable VM, histórico compacto, recuperação de relatórios e experiência operacional na interface web.

## Contexto

O endpoint legado `/vulns/export` aceita um limite inferior (`since`), mas não oferece um limite superior equivalente para reconstruir um período histórico encerrado. Em uma execução referente a julho, isso fez a coleta incluir grande volume posterior ao fim solicitado para depois descartá-lo localmente. Além disso, exports pequenos e grandes podem permanecer em `PROCESSING` sem disponibilizar chunks por muito tempo.

Os testes controlados mostraram também que `properties` seletivas não reduziram o tempo neste tenant. Portanto, essa opção continuará desabilitada por padrão e não será tratada como solução de desempenho até haver nova validação.

## Objetivos

- Preservar o comportamento confiável do relatório mensal automático executado no primeiro dia do mês.
- Evitar exportar dados posteriores ao fim de um período histórico quando houver uma fonte delimitada adequada.
- Preferir snapshots compactos já persistidos para regenerar relatórios passados.
- Manter os dados necessários para todas as tabelas, Top 5, hosts, tags e comparativos sem reter arquivos brutos volumosos.
- Tornar exports presos visíveis, canceláveis e diagnosticáveis sem confundir lentidão com erro de autenticação.
- Nunca apresentar uma reconstrução histórica como se fosse um snapshot exato quando a plataforma já alterou o estado dos findings.

## Decisão de arquitetura

Cada execução escolherá explicitamente uma rota de coleta:

| Situação | Rota | Resultado esperado |
|---|---|---|
| Relatório automático do mês anterior, executado no primeiro dia | Export VM legado | Snapshot mensal autoritativo, persistido antes da limpeza dos brutos |
| Relatório pontual terminando no momento atual | Export VM legado | Janela móvel; o `since` não causa excedente posterior ao fim |
| Regeneração com snapshot compacto do mesmo cliente e período | Replay do snapshot | Sem nova coleta; reproduz os dados persistidos |
| Período histórico encerrado, sem snapshot, com Inventory API habilitada | Reconstrução histórica híbrida | ACTIVE/RESURFACED delimitados pela Inventory API; FIXED coletado e recortado separadamente |
| Inventory API indisponível ou desabilitada | Política configurável | Falhar de forma clara ou usar legado com aviso explícito de reconstrução aproximada |

O modo híbrido é necessário porque a busca delimitada da Inventory API demonstrou equivalência para findings ativos/ressurgidos no teste de julho, mas os findings corrigidos ainda apresentaram diferença de estado temporal. A fonte legada continuará sendo a referência para FIXED até a equivalência ser comprovada em vários clientes e períodos.

## Fluxo de dados

1. Resolver cliente, período e tipo de execução.
2. Consultar o PostgreSQL por um snapshot compacto compatível.
3. Escolher a rota com uma decisão determinística e registrá-la no manifesto.
4. Coletar somente as fontes necessárias.
5. Normalizar todas as fontes para o mesmo modelo de finding.
6. Enriquecer dados da Inventory API por meio de um catálogo compacto de plugins alimentado por exports legados anteriores.
7. Gerar os datasets dos relatórios e persistir o snapshot compacto em uma única transação.
8. Somente após confirmar a persistência, remover chunks e arquivos intermediários pesados.

## Catálogo compacto de plugins

O catálogo será separado por tenant/cliente e guardará metadados necessários ao relatório: plugin ID, nome normalizado, família, descrição, sinopse, solução, referências, VPR/CVSS e indicadores/frameworks de exploração.

A associação por nome só poderá ocorrer quando for unívoca. Nomes ausentes ou ambíguos permanecerão sem enriquecimento e produzirão um aviso de qualidade; o sistema nunca escolherá silenciosamente um plugin incorreto.

## Snapshot compacto

O snapshot mensal deve conter fatos normalizados suficientes para regenerar os documentos, e não o JSON bruto integral. Ele inclui identidade da vulnerabilidade, estado e datas, severidade, VPR/CVSS, ativos e endpoints, evidências necessárias, associação às tags usadas, referências ao catálogo de plugins e proveniência da coleta.

Saídas DOCX e histórico compacto são permanentes conforme retenção. Chunks, manifests parciais e datasets intermediários são temporários.

## Resiliência operacional

- Separar limite de “sem progresso” do tempo total permitido.
- Para execução manual, sugerir 15 minutos sem novo chunk como padrão; para automática, permitir valor maior configurável.
- Cancelamento automático somente para UUID criado pela execução atual. Export reutilizado nunca será cancelado automaticamente.
- Exibir UUID, origem, estado, chunks concluídos e último progresso na interface.
- Oferecer “Cancelar export e tentar novamente” com confirmação e estratégia de nova tentativa explícita.
- Classificar timeout, cancelamento e indisponibilidade transitória como `TENABLE_TEMPORARY` e `retryable=true`.

## Experiência na interface

- Configuração por cliente para habilitar a reconstrução histórica beta, desabilitada por padrão durante o piloto.
- Aviso antes de gerar um período histórico sem snapshot.
- Selo `RECONSTRUÍDO` e explicação no registro do relatório quando os dados não forem um snapshot exato.
- Indicação da rota usada: snapshot, VM legado ou Inventory híbrido.
- Status de export sem progresso e ação de cancelamento usando o UUID correto.

## Segurança e privacidade

- Credenciais continuam exclusivamente em arquivos locais ignorados pelo Git.
- Logs e manifests não armazenam access key, secret key, e-mail, hostname ou IP além do necessário para a operação já autorizada.
- O catálogo e os snapshots respeitam o isolamento por cliente/tenant.
- Nenhuma coleta real fará parte da suíte automatizada; testes ao vivo exigem confirmação explícita.

## Critérios de aceite

- O comportamento padrão atual permanece legado até a ativação por cliente.
- Regenerar um período com snapshot não cria novo export.
- A busca histórica ativa/ressurgida respeita início e fim na origem.
- Contagens por severidade, estado, ativos e Top 5 coincidem entre a rota nova e a referência nos testes de homologação.
- Findings FIXED não migram para a Inventory API até atingirem equivalência comprovada.
- Nenhum enriquecimento ambíguo é aplicado silenciosamente.
- Um export manual sem progresso é diagnosticado e pode ser cancelado com segurança.
- A limpeza de brutos só ocorre depois de confirmar snapshot compacto e documentos publicados.

## Fora de escopo nesta entrega

- Substituir completamente o export VM legado.
- Usar `properties` seletivas por padrão.
- Usar Workbench, endpoint descontinuado e inadequado para alto volume recorrente.
- Considerar relatórios PDF da Tenable como fonte primária dos documentos Word.
- Alterar regras de negócio, textos ou estrutura visual dos relatórios.
