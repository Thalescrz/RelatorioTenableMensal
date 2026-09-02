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

  return { createLatestRequestGuard };
}));
