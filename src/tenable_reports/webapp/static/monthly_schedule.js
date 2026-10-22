(function (root, factory) {
  const helpers = factory();
  if (typeof module === "object" && module.exports) module.exports = helpers;
  else root.TenableMonthlySchedule = helpers;
}(typeof globalThis !== "undefined" ? globalThis : this, function () {
  const taskLabels = {
    MISSING: "Não instalada",
    SYNCHRONIZED: "Sincronizada",
    DISABLED: "Desativada",
    DIVERGENT: "Divergente",
    UNAVAILABLE: "Indisponível",
    ERROR: "Erro de consulta",
  };

  function scheduleView(payload = {}) {
    const config = payload.config || {};
    const genericScheduler = payload.scheduler || null;
    const task = genericScheduler || payload.windows_task || {};
    const count = Number(payload.eligible_client_count || 0);
    return {
      policyLabel: config.enabled ? "Ativa" : "Inativa",
      taskLabel: taskLabels[task.status] || "Desconhecida",
      backend: task.backend || (payload.windows_task ? "WINDOWS_TASK_SCHEDULER" : null),
      nextRunAt: payload.next_run_at || null,
      competence: payload.competence || "—",
      eligibleClientCopy: `${count} ${count === 1 ? "cliente elegível" : "clientes elegíveis"}`,
    };
  }

  return { scheduleView };
}));
