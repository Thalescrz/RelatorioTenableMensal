(function (root, factory) {
  const helpers = factory();
  if (typeof module === "object" && module.exports) module.exports = helpers;
  else root.TenableClientSelection = helpers;
}(typeof globalThis !== "undefined" ? globalThis : this, function () {
  function normalizeText(value) {
    return String(value || "").trim().toLowerCase();
  }

  function filterClients(clients, { query = "", analystId = "all" } = {}) {
    const normalizedQuery = normalizeText(query);
    return (Array.isArray(clients) ? clients : []).filter(client => {
      const matchesQuery = !normalizedQuery || [
        client.display_name,
        client.client_id,
        client.tenant_id,
      ].some(value => normalizeText(value).includes(normalizedQuery));
      const responsibleId = client.responsible_analyst_id;
      const matchesAnalyst = analystId === "all"
        || (analystId === "unassigned" ? !responsibleId : responsibleId === analystId);
      return matchesQuery && matchesAnalyst;
    });
  }

  function selectionForVisibleClients(currentSelection, visibleIds, selected) {
    const current = [];
    const seen = new Set();
    for (const clientId of Array.isArray(currentSelection) ? currentSelection : []) {
      if (!seen.has(clientId)) {
        seen.add(clientId);
        current.push(clientId);
      }
    }
    const visible = Array.isArray(visibleIds) ? visibleIds : [];
    if (!selected) {
      const visibleSet = new Set(visible);
      return current.filter(clientId => !visibleSet.has(clientId));
    }
    for (const clientId of visible) {
      if (!seen.has(clientId)) {
        seen.add(clientId);
        current.push(clientId);
      }
    }
    return current;
  }

  function resolveResponsibleAnalystValue(draftValue, persistedValue) {
    return draftValue === undefined ? (persistedValue || "") : draftValue;
  }

  function mergeSavedClient(clients, savedClient) {
    const current = Array.isArray(clients) ? clients : [];
    if (!savedClient || !savedClient.client_id) return [...current];
    const index = current.findIndex(
      client => client.client_id === savedClient.client_id
    );
    if (index < 0) return [...current, { ...savedClient }];
    return current.map((client, position) => (
      position === index ? { ...client, ...savedClient } : client
    ));
  }

  function conflictingJobsByClient(jobs) {
    const activeStatuses = new Set([
      "QUEUED",
      "RUNNING",
      "WAITING_WAS_DECISION",
      "INTERRUPT_REQUESTED",
    ]);
    const conflicts = {};
    for (const job of Array.isArray(jobs) ? jobs : []) {
      if (!job?.client_id || !activeStatuses.has(job.status)) continue;
      if (!conflicts[job.client_id]) conflicts[job.client_id] = { ...job };
    }
    return conflicts;
  }

  return {
    filterClients,
    selectionForVisibleClients,
    resolveResponsibleAnalystValue,
    mergeSavedClient,
    conflictingJobsByClient,
  };
}));
