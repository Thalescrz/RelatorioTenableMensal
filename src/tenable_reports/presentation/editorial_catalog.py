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
    "O OWASP top 10 é um relatório atualizado regularmente que descreve questões de "
    "segurança focado em aplicativos web, com foco nos 10 (dez) riscos mais críticos. "
    "Este relatório é elaborado por uma equipe de especialistas em segurança de todo "
    "o mundo. O OWASP refere-se ao Top 10 como um “documento de conscientização” e "
    "recomenda que todas as empresas incorporem o relatório em seus processos, a fim "
    "de minimizar e/ou mitigar os riscos de segurança como o Tenable.io Web App "
    "Scanning oferecendo esse cruzamento de informações com verificação de "
    "vulnerabilidades abrangente e precisa, de acordo com as essas fragilidades "
    "encontradas no ambiente."
)

OWASP_TABLES = (
    "Nas tabelas abaixo serão exibidas as aplicações WEB que se encaixam no OWASP "
    "top 10, de acordo com as fragilidades encontrada pelo sensor WAS, onde a coluna "
    "“instâncias” é a quantidade de URLs encontradas, por determinada técnica de "
    "ataque (plugin ID) em um FQDN específico que estão vulneráveis. Para mais "
    "detalhes a respeito é necessário visitar a plataforma do Tenable.io, pela grade "
    "granularidade de informações que podem ser coletadas."
)

OWASP_EMPTY_MONTH = (
    "Neste mês não foram identificadas vulnerabilidades relacionadas a esta "
    "categoria OWASP."
)

OWASP_CATEGORIES = (
    (
        "A01 – Broken Access Control (Falha no controle de Acesso)",
        "O controle de acesso reforça a política de forma que os usuários não possam agir fora de suas permissões pretendidas. As falhas geralmente levam à divulgação não autorizada de informações, modificação ou destruição de todos os dados ou à execução de uma função comercial fora dos limites do usuário.",
    ),
    (
        "A02 - Cryptographic Failures (Falhas de criptografia)",
        "A primeira coisa é determinar as necessidades de proteção dos dados em trânsito e em repouso. Por exemplo, senhas, números de cartão de crédito, registros de saúde, informações pessoais e segredos comerciais exigem proteção extra, principalmente se esses dados estiverem sob leis de privacidade, por exemplo, Regulamento Geral de Proteção de Dados da UE (GDPR) ou regulamentos, por exemplo, proteção de dados financeiros como o padrão de segurança de dados PCI (PCI DSS).",
    ),
    (
        "A03 – Injection (Injeção)",
        "Os ataques de injeção acontecem quando dados não confiáveis são enviados a um intérprete de código por meio de uma entrada de formulário ou algum outro envio de dados a aplicativo web. Por exemplo, um invasor pode inserir o código do banco de dados SQL em um formulário que espera um nome de usuário em texto não criptografado. Se essa entrada não estiver devidamente protegida, isso resultará na execução do código SQL.",
    ),
    (
        "A04 – Insecure Design (Design Inseguro)",
        "O design inseguro é uma categoria ampla que representa diferentes pontos fracos, expressos como “projeto de controle ausente ou ineficaz”. O design inseguro não é a fonte de todas as outras 10 principais categorias de risco. Há uma diferença entre design inseguro e implementação insegura. Nós diferenciamos entre falhas de design e defeitos de implementação por um motivo, eles têm causas e remediações diferentes. Um design seguro ainda pode ter defeitos de implementação levando a vulnerabilidades que podem ser exploradas. Um design inseguro não pode ser corrigido por uma implementação perfeita, pois, por definição, os controles de segurança necessários nunca foram criados para se defender contra ataques específicos. Um dos fatores que contribuem para o design inseguro é a falta de perfil de risco de negócios inerente ao software ou sistema que está sendo desenvolvido e, portanto, a falha em determinar qual nível de design de segurança é necessário.",
    ),
    (
        "A05 – Security Misconfiguration (Configuração incorreta de segurança)",
        "A configuração incorreta de segurança é a vulnerabilidade mais comum, e geralmente é o resultado do uso desses elementos padrões ou das exibições de erros excessivamente detalhados. Por exemplo, um aplicativo pode mostrar a um usuário erros em demasiados podendo assim revelar vulnerabilidades no mesmo.",
    ),
    (
        "A06 - Vulnerable and Outdated Components (Componentes vulneráveis e desatualizados)",
        "Muitos desenvolvedores voltados para aplicações web, que são modernos, usam componentes como bibliotecas e estruturas em seus aplicativos. Esses componentes são peças de software que ajudam os desenvolvedores a evitar trabalho redundante e fornecem a funcionalidade necessária. Exemplos comuns incluem estruturas de front-end como React e bibliotecas menores que costumavam adicionar ícones de compartilhamento ou teste a/b. Alguns invasores procuram vulnerabilidades nesses componentes que podem ser usados para orquestrar ataques.",
    ),
    (
        "A07 - Identification and Authentication Failures (Falhas de identificação e autenticação)",
        "Vulnerabilidades em sistemas de autenticação (login) podem dar aos invasores acesso a contas de usuários e até mesmo a capacidade de comprometer um sistema inteiro usando uma conta de administrador. Por exemplo, um invasor pode pegar uma lista contendo milhares de combinações de nome de usuário/senhas conhecidos obtidos durante uma violação de dados e usar um script para tentar todas essas combinações em um sistema de login para ver se há alguma que funcione.",
    ),
    (
        "A08 - Software and Data Integrity Failures (Falhas de software e integridade dos dados)",
        "Falhas de software e integridade de dados estão relacionadas a código e infraestrutura que não protegem contra violações de integridade. Um exemplo disso é quando um aplicativo depende de plugins, bibliotecas ou módulos de fontes, repositórios e redes de distribuição de conteúdo (CDNs) não confiáveis. Um pipeline de CI/CD inseguro pode introduzir o potencial de acesso não autorizado, código malicioso ou comprometimento do sistema. Por fim, muitos aplicativos agora incluem a funcionalidade de atualização automática, onde as atualizações são baixadas sem verificação de integridade suficiente e aplicadas ao aplicativo confiável anteriormente. Os invasores podem carregar suas próprias atualizações para serem distribuídas e executadas em todas as instalações. Outro exemplo é onde objetos ou dados são codificados ou serializados em uma estrutura que um invasor pode ver e modificar é vulnerável à desserialização insegura.",
    ),
    (
        "A09 - Security Logging and Monitoring Failures (Falhas de registro e monitoramento de segurança)",
        "Muitos aplicativos web não estão realizando etapas suficientes para detectar violações de dados. O tempo médio de descoberta de uma violação é de cerca de 200 dias após sua ocorrência. Isso dá aos invasores muito tempo para causar danos antes que haja qualquer resposta. O OWASP recomenda que os desenvolvedores da web implementem o registro e o monitoramento, bem como os planos de resposta a incidentes, para garantir que estejam cientes dos ataques aos seus aplicativos.",
    ),
    (
        "A10 - Server-Side Request Forgery ou SSRF (Falsificação de solicitação via o lado do servidor)",
        "As falhas de SSRF ocorrem sempre que um aplicativo da Web está buscando um recurso remoto sem validar a URL fornecida pelo usuário. Ele permite que um invasor force o aplicativo a enviar uma solicitação criada para um destino inesperado, mesmo quando protegido por um firewall, VPN ou outro tipo de lista de controle de acesso à rede (ACL). Como os aplicativos da web modernos fornecem aos usuários finais recursos convenientes, buscar uma URL se torna um cenário comum. Como resultado, a incidência de SSRF está aumentando. Além disso, a gravidade do SSRF está aumentando devido aos serviços em nuvem e à complexidade das arquiteturas.",
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

OS_GRAPH = (
    "O gráfico de sistemas operacionais mais comuns fornece (Abaixo) um gerenciamento "
    "usando porcentagens dos diferentes sistemas operacionais encontrados no ambiente, "
    "auxiliando no planejamento de tarefas e correções. Assim a equipe técnica pode "
    "usar dessas informações para ter uma ideia de quantas horas de trabalho seriam "
    "necessárias, com base no volume de ativos, para criar tarefas de correção."
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
