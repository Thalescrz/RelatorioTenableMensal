(function (root, factory) {
  const helpers = factory();
  if (typeof module === "object" && module.exports) module.exports = helpers;
  else root.TenableReportRequestGuard = helpers;
}(typeof globalThis !== "undefined" ? globalThis : this, function () {
  function createLatestRequestGuard() {
    let revision = 0;
    let clientId = null;

    return {
      begin(nextClientId) {
        revision += 1;
        clientId = String(nextClientId || "");
        return { revision, clientId };
      },
      invalidate() {
        revision += 1;
        clientId = null;
      },
      isCurrent(request) {
        return Boolean(
          request
          && request.revision === revision
          && request.clientId === clientId
        );
      },
      currentClientId() {
        return clientId;
      },
    };
  }

  function reportExecutionTimestamp(report) {
    if (report && report.executed_at) return report.executed_at;
    const documents = report && Array.isArray(report.documents)
      ? report.documents
      : [];
    const completed = documents.find(document => document && document.ended_at);
    if (completed) return completed.ended_at;
    const published = documents.find(document => document && document.created_at);
    return published ? published.created_at : null;
  }

  function reportExecutionCopy(report, formatter) {
    const timestamp = reportExecutionTimestamp(report);
    if (!timestamp) return "";
    const formatted = String(formatter(timestamp)).replace(/,\s*/, " às ");
    return `Executado em: ${formatted}`;
  }

  return { createLatestRequestGuard, reportExecutionCopy };
}));
