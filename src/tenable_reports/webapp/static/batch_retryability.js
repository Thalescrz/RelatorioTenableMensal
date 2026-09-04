(function (root, factory) {
  const helpers = factory();
  if (typeof module === "object" && module.exports) module.exports = helpers;
  else root.TenableBatchRetryability = helpers;
}(typeof globalThis !== "undefined" ? globalThis : this, function () {
  function retryabilityView(job) {
    if (typeof job?.retryable !== "boolean") {
      return {
        visible: false,
        label: "",
        tone: "",
        effectiveCode: "",
        recordedCopy: "",
        reason: "",
      };
    }
    const effectiveCode = String(
      job.effective_error_code || job.error_code || ""
    );
    const recordedCode = String(job.recorded_error_code || "");
    return {
      visible: true,
      label: job.retryable ? "Retentável" : "Não retentável",
      tone: job.retryable ? "retryable" : "non-retryable",
      effectiveCode,
      recordedCopy: recordedCode && recordedCode !== effectiveCode
        ? `Registrado originalmente como ${recordedCode}.`
        : "",
      reason: String(job.retryability_reason || ""),
    };
  }

  return { retryabilityView };
}));
