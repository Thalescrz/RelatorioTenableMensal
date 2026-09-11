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

  function comparableDashboardState(value) {
    if (!value || typeof value !== "object" || Array.isArray(value)) {
      return JSON.stringify(value);
    }
    const { server_time: _serverTime, ...stableState } = value;
    return JSON.stringify(stableState);
  }

  function hasDashboardStateChanged(previous, next) {
    return comparableDashboardState(previous) !== comparableDashboardState(next);
  }

  function createAdaptivePoller({
    refresh,
    hasActiveWork,
    isHidden = () => false,
    schedule = (callback, delay) => setTimeout(callback, delay),
    cancel = token => clearTimeout(token),
    activeIntervalMs = 3000,
    idleIntervalMs = 15000,
  }) {
    let started = false;
    let timer = null;

    function clearScheduled() {
      if (timer === null) return;
      cancel(timer);
      timer = null;
    }

    function scheduleNext() {
      clearScheduled();
      if (!started || isHidden()) return null;
      const delay = hasActiveWork() ? activeIntervalMs : idleIntervalMs;
      timer = schedule(() => {
        timer = null;
        Promise.resolve()
          .then(refresh)
          .catch(() => undefined)
          .finally(scheduleNext);
      }, delay);
      return delay;
    }

    function start() {
      if (started) return scheduleNext();
      started = true;
      return scheduleNext();
    }

    function stop() {
      started = false;
      clearScheduled();
    }

    async function handleVisibilityChange() {
      clearScheduled();
      if (!started || isHidden()) return null;
      try {
        await refresh();
      } finally {
        scheduleNext();
      }
      return null;
    }

    return {
      start,
      stop,
      reschedule: scheduleNext,
      handleVisibilityChange,
      isScheduled: () => timer !== null,
    };
  }

  return {
    createRefreshCoordinator,
    hasDashboardStateChanged,
    createAdaptivePoller,
  };
}));
