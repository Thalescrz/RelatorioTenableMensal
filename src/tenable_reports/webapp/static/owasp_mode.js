(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.owaspModeHelpers = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  const MODES = new Set(["inclusive", "console_exclusive"]);

  function normalizeOwaspMode(value) {
    const normalized = String(value || "").trim().toLowerCase();
    return MODES.has(normalized) ? normalized : "inclusive";
  }

  function owaspModeView({ wasEnabled, savedMode } = {}) {
    return {
      value: normalizeOwaspMode(savedMode),
      disabled: !Boolean(wasEnabled),
    };
  }

  return { normalizeOwaspMode, owaspModeView };
});
