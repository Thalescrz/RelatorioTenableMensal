"""Texto editorial preservado dos quatro relatórios DOCX de referência.

Este módulo não contém texto gerado pelo modelo. As constantes abaixo foram
transcritas dos parágrafos que se repetem nos documentos fornecidos pelo
usuário. Apenas datas, nomes de cliente e dados técnicos são interpolados pelo
gerador.
"""

OBJECTIVE = (
    "Este documento visa apresentar o relatório de vulnerabilidades da solução "
    "Tenable durante o período mensal corrente, conforme descrito no item "
    "2.4.3.7.2 do Apenso I – Requisitos da Área Técnica do Edital do Pregão "
    "Eletrônico TRT8 nº 04/2022."
)

NESSUS_SENSOR = (
    "As varreduras do Nessus, fazem uso de programas leves e de baixo consumo "
    "que você instala localmente nos hosts, ou através de sensores on-premises. "
    "Eles coletam dados de vulnerabilidades e conformidade dos sistemas, "
    "relatando informações coletadas ao gerenciador na nuvem do Tenable.io para "
    "análise. Os sensores Nessus são projetados para causar um impacto mínimo no "
    "sistema e na rede, oferecendo o benefício de acesso direto a todos os hosts "
    "sem interromper seus usuários finais."
)

VULNERABLE_ENVIRONMENT = (
    "Aplicativos e dispositivos vulneráveis na rede representam grandes riscos, "
    "como vulnerabilidades encontradas, como softwares desatualizados, suscetíveis "
    "a buffer overflows, serviços habilitados que possam causar risco etc. São os "
    "pontos fracos na rede que podem ser explorados facilmente. Assim as "
    "Organizações que não procuram continuamente por tais fragilidades em seus "
    "sistemas, tratando dessas falhas proativamente, que foram descobertas, podem "
    "provavelmente ter sua rede comprometida e dados comprometidos. Este relatório "
    "fornece uma visão geral detalhada dessas vulnerabilidades, podendo ajudar a "
    "identificar o problema, priorizando correções e acompanhando o seu progresso."
)

OVERVIEW_SUFFIX = (
    ", sobre as Vulnerabilidades, classificando-as com uma visão geral do parque "
    "tecnológico, podendo auxiliar no rastreamento e consequentemente em sua "
    "mitigação. Essa matriz abaixo apresenta informações de forma resumida por "
    "gravidade. Onde a linha em vermelho é a informação crítica, o laranja é alta, "
    "amarela é média e o verde tem a severidade baixa."
)

OVERVIEW_COLUMN_TEXTS = (
    "Na coluna “Mitigado”, exibe o número total de vulnerabilidades mitigadas.",
    "Na coluna “Não Mitigados”, exibe o número total de vulnerabilidades que ainda não foram mitigadas, logo, requerem uma atenção.",
    "Na coluna “Explorável”, exibe o número dessas vulnerabilidades não mitigadas que são conhecidas por serem exploráveis.",
    "Na coluna “Patchs disponíveis”, exibe o número das vulnerabilidades não mitigadas e exploráveis que tiveram um patch disponível por mais de 30 (trinta) dias.",
)

TOP_ASSETS_INTRO = (
    "Abaixo será apresentada uma lista dos 10 (Dez) principais Ativos, que estão "
    "mais vulneráveis em risco de exploração. Essas informações são filtradas por "
    "vulnerabilidades de gravidade alta ou crítica, podendo ser exploradas, sendo "
    "classificadas por fragilidades em algum software do sistema."
)

TOP_ASSETS_PRIORITY = (
    "Os dados sobre essas vulnerabilidades detectadas permitem que a equipe de TI "
    "do Órgão priorize os esforços na correção, sendo assim capazes de identificar "
    "facilmente as principais vulnerabilidades para mitigação, a fim de reduzir a "
    "superfície de ataques."
)

PRINCIPAL_VULNERABILITIES_INTRO = (
    "Nesta sessão será descrito algumas das principais vulnerabilidades corrigidas "
    "(mitigadas), não mitigadas e ativas, ao longo do tempo que este relatório se "
    "propôs a coletar essas informações. Destacando o progresso da mitigação e "
    "fornecendo mais detalhes sobre essas fragilidades. Mostrando também as "
    "principais vulnerabilidade Mitigadas, que por sua vez foram corrigidas por "
    "algum patch de atualização, exibindo-as em ordem por severidades."
)

FIXED_REMINDER = (
    "Lembrando que para uma vulnerabilidade ser marcada como corrigida, pela "
    "ferramenta, a vulnerabilidade estava presente em um ativo, mas agora não foi "
    "mais encontrada."
)

TOP5_VM_INTRO = (
    "Abaixo seguem informações sobre essas vulnerabilidades encontradas, no período "
    "supracitado, os assets que são afetados e a solução do problema. Aqui foi "
    "levado em consideração as 5 (cinco) fragilidades de ativos que são considerados "
    "pela ferramenta, não como as mais críticas, mas sim as com VPRs (Vulnerability "
    "Priority Rating) mais alto, pois a TENABLE entende e recomenda que o correto é "
    "remediar as vulnerabilidades com valores de VPR maiores, independentes se "
    "tiverem suas severidades alta, crítica, média ou baixa, onde por sua vez NÃO "
    "FORAM MITIGADAS."
)

WAS_SENSOR = (
    "O monitoramento pelo sensor WAS (Web Application Scanning), fornece informações "
    "baseadas em serviços da WEB no ambiente, estes serviços WEB acabam sendo uma "
    "tecnologia que são implementadas de várias maneiras. As vulnerabilidades de "
    "serviços web, dentro da plataforma da Tenable são relacionadas e exibidas de "
    "maneira fácil de entender. Assim os analistas podem ver determinadas "
    "vulnerabilidades baseadas em portas, plugins e protocolos, assim como "
    "atividades de serviços da Web que saem para o mundo exterior, e serviços que "
    "estão presentes só que com vulnerabilidades já conhecidas."
)

WAS_GLOBAL_HEALTH = (
    "Global Applications Health fornecem uma visão rápida das métricas acionáveis. "
    "O círculo externo do gráfico de anel do painel rastreia o Asset Exposure Score "
    "(AES) de quatro de seus aplicativos digitalizados e um pequeno outro segmento "
    "dos aplicativos restantes. O centro do gráfico de anéis do painel mostra sua "
    "pontuação geral do Cyber Exposure Score (CES) e a cor muda de acordo com sua "
    "nota atual do CES."
)

WAS_HEALTH_FACTORS = (
    "A lista dos principais fatores contribuintes no lado direito da interface do "
    "usuário mostra quais classificações de gravidade das aplicações verificadas "
    "estão presentes na sua instância do Tenable Web App Scanning. Esses itens "
    "contribuem para sua pontuação geral."
)

WAS_APPS = (
    "Assim apresentaremos a tabela abaixo, das aplicações que estão vulneráveis em "
    "risco de exploração. Essas informações são filtradas por vulnerabilidades de "
    "gravidade baixa, média, alta e crítica. Esses dados permitirão que os analistas "
    "priorizem os esforços de correção, sendo assim capazes de identificar "
    "facilmente as principais vulnerabilidades para mitigação afim de reduzir a "
    "superfície de ataque."
)

WAS_INFO_NOTE = (
    "OBS: Aqui não foram documentadas as vulnerabilidades categorizadas na "
    "plataforma como “informação”."
)

WAS_PLUGINS = (
    "Segue uma lista de vulnerabilidades detectadas pelos plugins utilizados no "
    "scanner do sensor, assim como o número de aplicativos relatados, com seus "
    "respectivos plugin ID."
)

OWASP_ORGANIZATION = (
    "O OWASP, Open Web Application Security Project é uma organização internacional "
    "sem fins lucrativos dedicada a segurança de aplicativos web, onde um de seus "
    "princípios fundamentais é que todos os seus materiais estejam disponíveis "
    "gratuitamente e facilmente acessíveis em seu site, tornando possível para "
    "qualquer pessoa melhorar a segurança de seus próprios aplicativos WEB."
)

OWASP_TOP10 = (
    "O OWASP Top 10 é um relatório atualizado regularmente que descreve questões de "
    "segurança focado em aplicativos web, com foco nos 10 (dez) riscos mais críticos. "
    "Este relatório é elaborado por uma equipe de especialistas em segurança de todo "
    "o mundo. O OWASP refere-se ao Top 10 como um “documento de conscientização” e "
    "recomenda que todas as empresas incorporem o relatório em seus processos, a fim "
    "de minimizar e/ou mitigar os riscos de segurança como o Tenable.io Web App "
    "Scanning oferecendo esse cruzamento de informações com verificação de "
    "vulnerabilidades abrangente e precisa, de acordo com as essas fragilidades "
    "encontradas no ambiente. Esta seção utiliza a edição OWASP Top 10:2025."
)

OWASP_TABLES = (
    "As tabelas abaixo apresentam as vulnerabilidades WEB classificadas pela "
    "Tenable nas categorias do OWASP Top 10:2025. A coluna “instâncias” representa "
    "a quantidade de ocorrências associadas ao plugin, e as linhas são ordenadas "
    "do maior para o menor VPR; registros sem VPR são apresentados ao final. "
    "Informações adicionais podem ser consultadas na plataforma Tenable."
)

OWASP_EMPTY_MONTH = (
    "Neste mês não foram identificadas vulnerabilidades relacionadas a esta "
    "categoria OWASP."
)

OWASP_EMPTY_PARTIAL = (
    "Não foram identificadas vulnerabilidades nesta categoria entre os plugins "
    "com classificação OWASP Top 10:2025 disponível."
)

OWASP_CATALOG_PARTIAL = (
    "A classificação OWASP Top 10:2025 estava disponível somente para parte dos "
    "plugins observados. As tabelas abaixo refletem exclusivamente essa cobertura."
)

OWASP_CATALOG_UNAVAILABLE = (
    "A classificação OWASP Top 10:2025 não estava disponível para os plugins "
    "observados neste período. A ausência de tabelas não representa ausência de "
    "vulnerabilidades WEB."
)

OWASP_CATEGORIES = (
    (
        "A01 - Broken Access Control (Falhas de Controle de Acesso)",
        "Reúne falhas que permitem ao usuário agir fora das permissões previstas, podendo resultar em acesso, alteração ou exclusão indevida de informações e funções.",
    ),
    (
        "A02 - Security Misconfiguration (Configuração Insegura)",
        "Abrange configurações ausentes, permissivas ou inconsistentes, recursos desnecessários e mensagens de erro excessivas que ampliam a superfície de ataque da aplicação.",
    ),
    (
        "A03 - Software Supply Chain Failures (Falhas na Cadeia de Suprimentos de Software)",
        "Considera riscos introduzidos por dependências, ferramentas, repositórios, processos de construção e distribuição que não sejam adequadamente inventariados, verificados e protegidos.",
    ),
    (
        "A04 - Cryptographic Failures (Falhas Criptográficas)",
        "Inclui proteção criptográfica ausente ou inadequada para dados sensíveis em trânsito e em repouso, bem como escolhas frágeis de algoritmos, chaves, protocolos e armazenamento.",
    ),
    (
        "A05 - Injection (Injeção)",
        "Ocorre quando dados não confiáveis são interpretados como comandos ou consultas, possibilitando a alteração da lógica esperada e a execução de operações não autorizadas.",
    ),
    (
        "A06 - Insecure Design (Design Inseguro)",
        "Representa controles de segurança ausentes ou ineficazes no desenho da solução. A correção depende de requisitos, modelagem de ameaças e padrões de projeto seguros, não apenas da implementação.",
    ),
    (
        "A07 - Authentication Failures (Falhas de Autenticação)",
        "Abrange problemas no estabelecimento e na manutenção da identidade e da sessão do usuário, incluindo controles insuficientes contra credenciais comprometidas e sequestro de sessão.",
    ),
    (
        "A08 - Software or Data Integrity Failures (Falhas de Integridade de Software ou Dados)",
        "Inclui código, atualizações, artefatos e dados críticos processados sem verificação suficiente de origem e integridade, permitindo alterações não autorizadas.",
    ),
    (
        "A09 - Security Logging and Alerting Failures (Falhas de Registro e Alerta de Segurança)",
        "Reúne deficiências de registro, correlação, monitoramento e alerta que dificultam a detecção, a investigação e a resposta tempestiva a atividades maliciosas.",
    ),
    (
        "A10 - Mishandling of Exceptional Conditions (Tratamento Inadequado de Condições Excepcionais)",
        "Abrange falhas ao prever, limitar e tratar condições excepcionais, permitindo estados inseguros, comportamento inesperado, indisponibilidade ou exposição indevida de informações.",
    ),
)

TOP5_WEB_INTRO = (
    "Nesta sessão haverá as principais vulnerabilidades que foram detectadas por um "
    "determinado uso de plugins pelo Web Application Scanning (WAS), bem como as "
    "recomendações para chegar na solução e por sua vez mitigando o problema, onde "
    "serão as 5 (cinco) primeiras que mais são críticas no ambiente, caso não haja "
    "as críticas citadas, segurar o grau de severidade proposto na ferramenta, como "
    "Alta, Média e baixa."
)
TOP5_WEB_EMPTY_MONTH = (
    "Neste mês não foram identificadas vulnerabilidades WEB não mitigadas "
    "para detalhamento neste item."
)

WAS_COLLECTION_UNAVAILABLE = (
    "Não foi possível concluir a coleta WEB neste período. Os dados VM deste "
    "relatório permanecem válidos; as tabelas desta seção não devem ser "
    "interpretadas como ausência de vulnerabilidades WEB."
)

EXPLOIT_FRAMEWORK_EMPTY_MONTH = (
    "Neste mês não foram identificadas vulnerabilidades exploráveis por frameworks "
    "conhecidos."
)

EMPTY_TABLE_MONTH = "Neste mês não foram identificados registros para este item."

SECURITY_INCREMENT = (
    "Para melhor incremento de segurança e proteção do ambiente é necessário a "
    "análise dos dados abaixo, onde mostrará uma síntese de informações levando em "
    "consideração os Sistemas operacionais dos ASSETS, para auxiliar no rastreamento "
    "da mitigação das vulnerabilidades:"
)

OS_COLUMNS = (
    "A coluna Mitigado exibe o número total de vulnerabilidades mitigadas, na “Não "
    "Mitigada” exibe o número total de vulnerabilidades que ainda não foram mitigadas, "
    "na coluna “Explorável” exibe porcentagem dessas vulnerabilidades não mitigadas "
    "que são conhecidas por serem exploráveis. Na coluna Patch disponível exibe a "
    "porcentagem de vulnerabilidade não mitigadas e exploráveis que tiveram o seu "
    "patch disponível por mais de 30 (trinta) dias."
)

CVSS = (
    "A análise da matriz de CVSS, que auxilia bastante no rastreio de mitigação, onde "
    "apresenta informações resumidas de vulnerabilidades por pontuação CVSS. O Common "
    "Vulnerability Scoring System (CVSS) é o padrão aberto da indústria para avaliar "
    "a gravidade dessas vulnerabilidades, levando em consideração a segurança do "
    "sistema operacional de um computador. Essa pontuação tem como a base CVSS, para "
    "tentar estabelecer uma medida de quanta preocupação uma vulnerabilidade garante, "
    "em comparação com outras vulnerabilidades. Uma pontuação base CVSS de 10,0 é a "
    "mais crítica."
)

CVSS_VPR_CORRELATION = (
    "Segue uma tabela abaixo, onde foi feito a correlação entre a pontuações CVSS3 e "
    "pontuações VPR para as vulnerabilidades presentes no órgão. As pontuações CVSSv3 "
    "são usadas no método tradicional de análise de risco, enquanto o VPR é um novo "
    "método baseado em análise de ciência de dados e modelagem de ameaças. Cada célula "
    "consiste em um mapeamento cruzado de pontuações CVSS e VPR."
)

HEATMAP = (
    "Essa tabela faz uma abordagem de mapa de calor, com o canto superior esquerdo "
    "contendo as vulnerabilidades com menor risco. Movendo-se para baixo e para a "
    "direita na matriz, as cores mudam de amarelo para vermelho à medida que os níveis "
    "de risco aumentam. Assim recomendamos mitigas os riscos mostrados nas células "
    "inferiores da direita e trabalhar nas células superiores da esquerda, já que as "
    "células inferiores da direita representam o maior risco."
)

VPR = (
    "O VPR (Vulnerability Priority Rating) exibe a contagem de vulnerabilidade, "
    "organizada pela categoria. Essa métrica dinâmica é proprietária da TENABLE que "
    "representa a gravidade de uma determinada falha e que tem grandes chances de ser "
    "exploradas. A Tenable recomenda fortemente corrigir as vulnerabilidades com o "
    "VPR mais alto primeiro. Esse algoritmo usa dados de várias fontes, como pesquisa "
    "proprietária, aprendizado baseado em máquina e inteligência de terceiros, "
    "incluindo Recorded Future."
)

SUMMARY_LIFECYCLE = (
    "Segue um resumo das vulnerabilidades por estado, que fornece aos analistas uma "
    "visão do seu ciclo de vida. O rastreio das vulnerabilidades é feito levando em "
    "consideração alguns estados como novo, ativo, corrigido e ressurgido. O que dá "
    "um melhor acompanhamento no progresso de esforços na mitigação de riscos."
)

SUMMARY_COLUMNS = (
    "Cada coluna representa um nível elevado de risco. A primeira coluna “Explorável” "
    "mostra a contagem de vulnerabilidades que são conhecidas por serem de fácil "
    "exploração, independentemente de sua gravidade. As próximas três colunas mostram "
    "contagens de vulnerabilidade com base na gravidade."
)

AGING = (
    "A tabela abaixo ajuda a gerenciar acordos de níveis de serviço, fornecendo uma "
    "visão das vulnerabilidades discriminadas por sua gravidade e idades. A idade da "
    "vulnerabilidade é determinada pelo tempo desde que a vulnerabilidade foi vista "
    "pela primeira vez."
)

FRAMEWORK = (
    "E por fim, os dados abaixo fornece um resumo das vulnerabilidades exploráveis por "
    "framework. Estruturas de exploração, como Metasploit e Canvas, são projetadas para "
    "detectar e explorar vulnerabilidades de software e hardware em sistemas de destino. "
    "Essa matriz ajuda as equipes de segurança a descobrir riscos que podem exigir "
    "priorização sobre outras vulnerabilidades. Os requisitos para este dados abaixo, "
    "são o Tenable.io “Vulnerability Management” (Nessus,NNM)."
)

BACK_COVER = "SUA MELHOR ALIADA NA JORNADA DA PROTEÇÃO DIGITAL."
