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

  function monthlyClientSelectionView(config = {}, clients = []) {
    const selected = Array.isArray(config.selected_client_ids)
      ? new Set(config.selected_client_ids) : null;
    return {
      mode: selected === null ? "all" : "selected",
      clients: clients.map(client => ({
        ...client,
        selected: selected === null || selected.has(client.client_id),
        eligible: Boolean(client.enabled && client.credentials_ready),
        reason: !client.enabled ? "Cliente inativo"
          : !client.credentials_ready ? "Credenciais pendentes" : "Elegível",
      })),
    };
  }

  function monthlyClientSelectionValue(mode, selectedIds) {
    return mode === "all" ? null : [...new Set(selectedIds)];
  }

  function monthlyClientSelectionPatch(saved, draft, knownIds) {
    if (saved === null || draft === null) {
      return saved === draft ? {} : { selected_client_ids: draft };
    }
    const known = new Set(knownIds);
    const visibleSaved = saved.filter(id => known.has(id));
    const changed = visibleSaved.length !== draft.length || visibleSaved.some(id => !draft.includes(id));
    return changed ? { selected_client_ids: draft } : {};
  }

  async function runWithDisabledButton(button, operation) {
    button.disabled = true;
    try {
      return await operation();
    } finally {
      button.disabled = false;
    }
  }

  function scheduleActionMessage(action) {
    const messages = {
      save: "A política foi salva. A tarefa do sistema operacional não foi alterada.",
      validate: "Configuração válida; nenhuma tarefa foi criada e nenhuma coleta foi iniciada.",
      apply: "A tarefa mensal foi criada ou atualizada no sistema operacional.",
      enable: "A tarefa mensal foi ativada no sistema operacional.",
      disable: "A tarefa mensal foi desativada no sistema operacional.",
    };
    return messages[action] || "Configuração mensal atualizada.";
  }

  return { scheduleView, monthlyClientSelectionView, monthlyClientSelectionValue, monthlyClientSelectionPatch, runWithDisabledButton, scheduleActionMessage };
}));
