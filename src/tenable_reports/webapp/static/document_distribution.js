(function (root, factory) {
  const helpers = factory();
  if (typeof module === "object" && module.exports) module.exports = helpers;
  else root.TenableDocumentDistribution = helpers;
}(typeof globalThis !== "undefined" ? globalThis : this, function () {
  const EMAIL_PATTERN = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;

  function normalizeRecipient(value) {
    const recipient = value && typeof value === "object" ? value : {};
    const normalized = {
      name: String(recipient.name || "").trim(),
      organization: String(recipient.organization || "").trim(),
      email: String(recipient.email || "").trim(),
    };
    if (!normalized.name) throw new Error("Informe o nome do destinatário.");
    if (!normalized.organization) throw new Error("Informe a organização do destinatário.");
    if (!EMAIL_PATTERN.test(normalized.email)) throw new Error("Informe um e-mail válido para o destinatário.");
    return normalized;
  }

  function copyRecipients(records) {
    return (Array.isArray(records) ? records : []).map(normalizeRecipient);
  }

  function addRecipient(records, value) {
    const current = copyRecipients(records);
    const recipient = normalizeRecipient(value);
    if (current.some(item => item.email.toLowerCase() === recipient.email.toLowerCase())) {
      throw new Error("Já existe um destinatário com este e-mail.");
    }
    return [...current, recipient];
  }

  function removeRecipient(records, index) {
    return copyRecipients(records).filter((_, position) => position !== Number(index));
  }

  return { normalizeRecipient, copyRecipients, addRecipient, removeRecipient };
}));
