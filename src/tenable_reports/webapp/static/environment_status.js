(function (root, factory) {
  const helpers = factory();
  if (typeof module === "object" && module.exports) module.exports = helpers;
  else root.TenableEnvironmentStatus = helpers;
}(typeof globalThis !== "undefined" ? globalThis : this, function () {
  const statusLabels = {
    READY: "Pronto",
    WARNING: "Atenção",
    BLOCKED: "Bloqueado",
  };

  const componentLabels = {
    platform: "Sistema operacional",
    font_profile: "Perfil tipográfico",
    libreoffice: "LibreOffice",
    postgresql: "PostgreSQL",
    storage: "Armazenamento",
    timezone: "Fuso horário",
    scheduler: "Agendador mensal",
  };

  function present(value, fallback = "Não detectado") {
    const normalized = String(value ?? "").trim();
    return normalized || fallback;
  }

  function component(key, source, detail) {
    const status = present(source?.status, "WARNING");
    return {
      key,
      label: componentLabels[key],
      status,
      statusLabel: statusLabels[status] || "Desconhecido",
      detail,
    };
  }

  function environmentView(payload = {}) {
    const platform = payload.platform || {};
    const fonts = payload.font_profile || {};
    const office = payload.libreoffice || {};
    const postgresql = payload.postgresql || {};
    const storage = payload.storage || {};
    const timezone = payload.timezone || {};
    const scheduler = payload.scheduler || {};
    const components = [
      component(
        "platform",
        platform,
        `${present(platform.system)} · ${present(platform.architecture)}`,
      ),
      component("font_profile", fonts, present(fonts.id)),
      component(
        "libreoffice",
        office,
        office.version
          ? `${office.version} · ${present(office.source, "origem não informada")}`
          : "Não detectado",
      ),
      component(
        "postgresql",
        postgresql,
        postgresql.configured ? "Configurado" : "Não configurado",
      ),
      component(
        "storage",
        storage,
        `${Number(storage.roots_checked || 0)} raiz(es) verificada(s)`,
      ),
      component("timezone", timezone, "Base IANA America/Fortaleza"),
      component(
        "scheduler",
        scheduler,
        `${present(scheduler.backend)} · ${present(scheduler.state)}`,
      ),
    ];
    return {
      overallStatus: payload.status || "WARNING",
      overallLabel: statusLabels[payload.status] || "Desconhecido",
      publicationLabel: payload.official_publication_ready
        ? "Publicação disponível"
        : "Publicação bloqueada",
      components,
      issues: Array.isArray(payload.issues) ? payload.issues : [],
    };
  }

  return { environmentView };
}));
