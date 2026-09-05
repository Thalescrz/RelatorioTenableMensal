(function (root, factory) {
  const helpers = factory();
  if (typeof module === "object" && module.exports) module.exports = helpers;
  else root.TenableBatchFamilyFilters = helpers;
}(typeof globalThis !== "undefined" ? globalThis : this, function () {
  function normalizeText(value) {
    return String(value || "").trim().toLowerCase();
  }

  function filterFamilyClients(
    clients,
    { status = "all", query = "", analystId = "all" } = {},
  ) {
    const normalizedQuery = normalizeText(query);
    return (Array.isArray(clients) ? clients : []).filter(client => {
      const matchesStatus = status === "all" || client.effective_status === status;
      const matchesQuery = !normalizedQuery || [
        client.display_name,
        client.client_id,
        client.tenant_id,
      ].some(value => normalizeText(value).includes(normalizedQuery));
      const responsibleId = client.responsible_analyst_id;
      const matchesAnalyst = analystId === "all"
        || (analystId === "unassigned" ? !responsibleId : responsibleId === analystId);
      return matchesStatus && matchesQuery && matchesAnalyst;
    });
  }

  function toggleFamilyFilter(currentStatus, requestedStatus) {
    return currentStatus === requestedStatus ? null : requestedStatus;
  }

  function filterPortfolioByFamily(
    clients,
    familyClients,
    { status = null, query = "", analystId = "all" } = {},
  ) {
    const portfolio = Array.isArray(clients) ? clients : [];
    if (!status) return portfolio;
    const familyIds = new Set(filterFamilyClients(familyClients, {
      status,
      query,
      analystId,
    }).map(client => client.client_id));
    return portfolio.filter(client => familyIds.has(client.client_id));
  }

  return { filterFamilyClients, filterPortfolioByFamily, toggleFamilyFilter };
}));
