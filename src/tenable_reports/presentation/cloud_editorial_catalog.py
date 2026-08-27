"""Catálogo editorial sanitizado do relatório Tenable Cloud Security aprovado."""

from __future__ import annotations


OBJECTIVE = (
    "Este documento visa apresentar o relatório de vulnerabilidades da solução "
    "Tenable durante o período mensal corrente."
)

CLOUD_OVERVIEW = (
    "Este relatório documenta as vulnerabilidades identificadas durante a análise "
    "de segurança realizada com a solução Tenable Cloud Security, uma plataforma "
    "de segurança baseada em nuvem projetada para fornecer visibilidade contínua, "
    "análise e gerenciamento de riscos em infraestruturas complexas."
)

CLOUD_INTEGRATION = (
    "O Tenable Cloud Security integra-se diretamente a ambientes de nuvem, como "
    "AWS, Azure e Google Cloud, permitindo a detecção proativa de vulnerabilidades, "
    "configurações incorretas e riscos de conformidade. Com recursos avançados de "
    "automação e análise, a solução avalia a postura de segurança em tempo real, "
    "identificando fraquezas que podem ser exploradas por atacantes."
)

REPORT_OBJECTIVES_INTRO = "Este relatório tem como objetivo:"
REPORT_OBJECTIVES = (
    "Identificar e classificar as vulnerabilidades detectadas em diversos serviços "
    "e componentes da infraestrutura de nuvem.",
    "Avaliar o impacto dessas vulnerabilidades no ambiente operacional e nos negócios.",
    "Fornecer recomendações práticas para mitigar ou corrigir as falhas de segurança, "
    "com base nas melhores práticas de segurança na nuvem.",
)

DETECTION_INTRO = (
    "O processo de detecção foi realizado utilizando a capacidade da plataforma de:"
)
DETECTION_CAPABILITIES = (
    "Monitoramento contínuo de ativos e serviços em nuvem, garantindo a detecção de "
    "novas ameaças conforme elas surgem.",
    "Análise contextual de risco, onde cada vulnerabilidade é avaliada com base no "
    "ambiente específico, priorizando aquelas que apresentam maior impacto.",
    "Integração com DevOps, que facilita a correção de problemas antes que o código "
    "seja implementado em produção, reduzindo significativamente o risco de falhas "
    "exploráveis em aplicações nativas de nuvem.",
)

TOP_HOSTS_INTRO = (
    "Nesta seção, destacamos os hosts mais vulneráveis identificados durante a "
    "análise, com foco nos que apresentam maior exposição e risco à segurança. "
    "Esses hosts foram priorizados com base na quantidade de vulnerabilidades "
    "críticas e altas detectadas, bem como na extensão de sua exposição à rede e "
    "ao impacto que poderiam causar em caso de exploração."
)

TOP_HOSTS_DETAILS = (
    "Cada host listado a seguir inclui informações detalhadas sobre seu sistema "
    "operacional, o número de vulnerabilidades críticas e de alto risco, software "
    "identificado, patches faltantes, e exposição à rede externa. Além disso, "
    "também fornecemos uma visão geral das portas expostas e o escopo de exposição "
    "da rede, o que facilita a compreensão do nível de risco associado."
)

TOP_IMAGES_INTRO = (
    "Esta seção destaca as 10 imagens de contêineres com o maior número de "
    "vulnerabilidades detectadas no ambiente. A análise inclui vulnerabilidades "
    "críticas e de alto risco que podem comprometer a segurança da infraestrutura, "
    "além de falhas em componentes essenciais que podem ser explorados. As imagens "
    "foram priorizadas com base no número de vulnerabilidades e na criticidade, "
    "permitindo uma ação rápida para mitigar os riscos relacionados."
)

TOP_IMAGES_TABLE_INTRO = (
    "A tabela a seguir apresenta as imagens mais vulneráveis e suas respectivas "
    "classificações de risco, permitindo uma visão clara das prioridades de correção."
)

TOP_CRITICAL_INTRO = (
    "Nesta seção, apresentamos as 5 (cinco) principais vulnerabilidades críticas "
    "(CVEs) identificadas no ambiente do cliente, classificadas com base no "
    "Vulnerability Priority Rating (VPR) da Tenable. O VPR é um sistema que avalia "
    "o risco de cada vulnerabilidade, levando em consideração diversos fatores, "
    "como o impacto potencial, facilidade de exploração e a relevância da "
    "vulnerabilidade no cenário atual de ameaças."
)

TOP_CRITICAL_PRIORITY = (
    "As vulnerabilidades listadas aqui são consideradas de altíssima prioridade e "
    "exigem correção imediata, pois representam os maiores riscos de segurança para "
    "o ambiente. A tabela a seguir fornece informações detalhadas sobre cada "
    "vulnerabilidade, incluindo seu impacto no sistema operacional, o software "
    "afetado e os hosts ou imagens de contêiner que estão vulneráveis."
)

DASHBOARD_INTRO = (
    "Nesta seção, apresentamos gráficos que fornecem uma visão geral rápida sobre "
    "o status de segurança do ambiente, com base nos dados coletados pelo Tenable "
    "Cloud Security. Esses gráficos ajudam a identificar rapidamente as áreas mais "
    "vulneráveis e a priorizar ações corretivas. A visualização desses dados "
    "facilita o monitoramento contínuo das vulnerabilidades e o acompanhamento do "
    "status de segurança."
)

WORKLOAD_STATUS = (
    "Esse gráfico exibe uma visão detalhada do status de vulnerabilidade dos "
    "workloads (cargas de trabalho) no ambiente. Ele oferece uma análise das "
    "máquinas categorizadas de acordo com a vulnerabilidade mais grave identificada. "
    "Essa visão permite priorizar as máquinas mais críticas para correção."
)

OPERATING_SYSTEM_STATUS = (
    "O gráfico abaixo apresenta a distribuição das máquinas com base no status do "
    "sistema operacional instalado. Questões relacionadas ao sistema operacional "
    "podem comprometer a segurança das workloads e devem ser tratadas com urgência "
    "através da atualização do SO para mitigar vulnerabilidades."
)

CONCLUSION_OVERVIEW = (
    "Este relatório apresenta uma visão detalhada das principais vulnerabilidades "
    "encontradas no ambiente do cliente, utilizando a solução avançada Tenable "
    "Cloud Security. A análise forneceu insights críticos sobre os pontos mais "
    "sensíveis da infraestrutura de nuvem, priorizando as máquinas, contêineres e "
    "sistemas operacionais com maior exposição a riscos. As principais "
    "vulnerabilidades foram classificadas com base no Vulnerability Priority Rating "
    "(VPR) da Tenable, garantindo que as falhas mais críticas sejam identificadas "
    "para correção imediata."
)

CONCLUSION_VALUE_INTRO = (
    "A solução Tenable Cloud Security demonstrou seu valor ao oferecer:"
)
CONCLUSION_VALUES = (
    "Monitoramento contínuo da segurança de workloads, máquinas virtuais e "
    "contêineres, proporcionando visibilidade em tempo real sobre o estado do ambiente.",
    "Análises baseadas em risco, permitindo priorizar correções de acordo com a "
    "criticidade das vulnerabilidades e a exposição dos ativos à rede.",
    "Automação de processos de detecção e análise, facilitando o acompanhamento de "
    "políticas de segurança em grandes infraestruturas de nuvem.",
)

CONCLUSION_PLATFORM = (
    "Este relatório é uma amostra das áreas mais críticas que precisam de atenção "
    "imediata. Para acessar todos os dados detalhados, recomenda-se que o cliente "
    "utilize a plataforma Tenable Cloud Security, onde é possível visualizar "
    "informações mais completas, realizar auditorias contínuas e acompanhar o "
    "progresso das correções de vulnerabilidades."
)

CONCLUSION_SUMMARY = (
    "Em resumo, a Tenable Cloud Security não apenas identifica as vulnerabilidades, "
    "mas também permite uma gestão eficiente do risco no ambiente de nuvem, "
    "ajudando a garantir a continuidade dos negócios com um nível de segurança "
    "elevado. A utilização dessa solução possibilita uma abordagem proativa, "
    "permitindo que sua empresa se proteja contra ameaças e mantenha uma postura de "
    "segurança robusta e atualizada."
)

EMPTY_TABLE_MONTH = "Neste mês não foram identificados registros para este item."
EMPTY_CRITICAL_MONTH = (
    "Neste mês não foram identificadas vulnerabilidades críticas para detalhamento."
)
EMPTY_CORRECTABLE_MONTH = (
    "Neste mês não foram identificadas vulnerabilidades com correção disponível."
)
SOURCE_UNAVAILABLE = (
    "Neste mês esta informação não pôde ser obtida pela API Tenable Cloud Security."
)
TRANSLATION_UNAVAILABLE = (
    "A tradução automática não pôde ser concluída; o texto original foi preservado."
)


def approved_cloud_editorial_paragraphs() -> tuple[str, ...]:
    """Textos genéricos preservados e aprovados para o Modelo Base."""

    return (
        OBJECTIVE,
        CLOUD_OVERVIEW,
        CLOUD_INTEGRATION,
        REPORT_OBJECTIVES_INTRO,
        *REPORT_OBJECTIVES,
        DETECTION_INTRO,
        *DETECTION_CAPABILITIES,
        TOP_HOSTS_INTRO,
        TOP_HOSTS_DETAILS,
        TOP_IMAGES_INTRO,
        TOP_IMAGES_TABLE_INTRO,
        TOP_CRITICAL_INTRO,
        TOP_CRITICAL_PRIORITY,
        DASHBOARD_INTRO,
        WORKLOAD_STATUS,
        OPERATING_SYSTEM_STATUS,
        CONCLUSION_OVERVIEW,
        CONCLUSION_VALUE_INTRO,
        *CONCLUSION_VALUES,
        CONCLUSION_PLATFORM,
        CONCLUSION_SUMMARY,
    )


__all__ = [
    "EMPTY_CORRECTABLE_MONTH",
    "EMPTY_CRITICAL_MONTH",
    "EMPTY_TABLE_MONTH",
    "SOURCE_UNAVAILABLE",
    "TRANSLATION_UNAVAILABLE",
    "approved_cloud_editorial_paragraphs",
]
