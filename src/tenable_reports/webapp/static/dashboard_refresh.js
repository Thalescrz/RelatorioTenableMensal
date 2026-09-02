(function (root, factory) {
  const helpers = factory();
  if (typeof module === "object" && module.exports) module.exports = helpers;
  else root.TenableDashboardRefresh = helpers;
}(typeof globalThis !== "undefined" ? globalThis : this, function () {
  function createRefreshCoordinator({ load, apply, onError }) {
    let inFlight = null;
    let followUpRequested = false;

    async function run() {
      do {
        followUpRequested = false;
        try {
          const value = await load();
          await apply(value);
        } catch (error) {
          await onError(error);
        }
      } while (followUpRequested);
    }

    function refresh({ ensureAfterCurrent = false } = {}) {
      if (inFlight) {
        if (ensureAfterCurrent) followUpRequested = true;
        return inFlight;
      }
      inFlight = run().finally(() => {
        inFlight = null;
      });
      return inFlight;
    }

    return {
      refresh,
      isRunning: () => inFlight !== null,
    };
  }

  return { createRefreshCoordinator };
}));
