(function (root, factory) {
  const helpers = factory();
  if (typeof module === "object" && module.exports) module.exports = helpers;
  else root.TenableDashboardAlerts = helpers;
}(typeof globalThis !== "undefined" ? globalThis : this, function () {
  function timestamp(value) {
    if (!value) return null;
    const parsed = Date.parse(value);
    return Number.isFinite(parsed) ? parsed : null;
  }

  function isAlertUnread(item, alertsReadBefore) {
    const cutoff = timestamp(alertsReadBefore);
    const occurredAt = timestamp(item?.at);
    if (cutoff === null || occurredAt === null) return true;
    return occurredAt > cutoff;
  }

  function unreadAlertItems(items, alertsReadBefore) {
    return items.filter(item => isAlertUnread(item, alertsReadBefore));
  }

  function hasUnreadWasRecovery(client, alertsReadBefore) {
    return (client?.was_recoveries || []).some(recovery => isAlertUnread(
      { at: recovery?.updated_at },
      alertsReadBefore,
    ));
  }

  function shouldPresentClientAsCompleted(client, alertsReadBefore) {
    const job = client?.job;
    if (!client?.latest_report || !job) return false;
    if (!["COMPLETE_WITH_WARNINGS", "PARTIALLY_COMPLETE", "FAILED"].includes(job.status)) {
      return false;
    }
    return !isAlertUnread(
      { at: job.ended_at || job.created_at },
      alertsReadBefore,
    );
  }

  function presentedClientProgress(client, alertsReadBefore) {
    if (shouldPresentClientAsCompleted(client, alertsReadBefore)) return 100;
    if (client?.job?.progress !== undefined && client?.job?.progress !== null) {
      return client.job.progress;
    }
    return client?.latest_report ? 100 : 0;
  }

  return {
    isAlertUnread,
    unreadAlertItems,
    hasUnreadWasRecovery,
    shouldPresentClientAsCompleted,
    presentedClientProgress,
  };
}));
