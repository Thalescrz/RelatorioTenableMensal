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

  return {
    filterClients,
    selectionForVisibleClients,
    resolveResponsibleAnalystValue,
  };
}));
