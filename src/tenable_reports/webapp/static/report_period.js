(function (root, factory) {
  const helpers = factory();
  if (typeof module === "object" && module.exports) module.exports = helpers;
  else root.TenableReportPeriod = helpers;
}(typeof globalThis !== "undefined" ? globalThis : this, function () {
  function periodRequest(type, values = {}) {
    const request = { mode: "manual" };
    if (type === "calendar") request.previous_calendar_month = true;
    if (type === "days") request.days = Number(values.days);
    if (type === "range") {
      request.start_date = String(values.start_date || "");
      request.end_date = String(values.end_date || "");
    }
    return request;
  }

  function localDate(iso, timezone) {
    return new Intl.DateTimeFormat("pt-BR", {
      timeZone: timezone,
      day: "2-digit",
      month: "2-digit",
      year: "numeric",
    }).format(new Date(iso));
  }

  function formatPeriodPreview(payload = {}) {
    const periods = payload.periods || [];
    if (!periods.length) return "Período indisponível.";
    return periods.map(period => (
      `${period.period_id} · ${period.timezone} · `
      + `${localDate(period.start_at, period.timezone)} até `
      + `${localDate(period.end_at, period.timezone)} (fim exclusivo)`
    )).join(" | ");
  }

  return { periodRequest, formatPeriodPreview };
}));
