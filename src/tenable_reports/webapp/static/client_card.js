(function (root, factory) {
  const helpers = factory();
  if (typeof module === "object" && module.exports) module.exports = helpers;
  else root.TenableClientCard = helpers;
}(typeof globalThis !== "undefined" ? globalThis : this, function () {
  function clientModuleSummary(client) {
    const modules = ["VM"];
    if (client?.was_enabled) modules.push("WAS");
    if (client?.cloud_enabled) modules.push("CLOUD");
    return { label: "Módulos ativos", modules };
  }

  function reconcileClientCards(container, clients, { createCard, updateCard }) {
    const existing = new Map(
      Array.from(container.children).map(node => [node.dataset.client, node]),
    );
    const ordered = [];

    for (const client of clients) {
      let card = existing.get(client.client_id);
      if (card) {
        existing.delete(client.client_id);
        updateCard(card, client);
      } else {
        card = createCard(client);
      }
      ordered.push(card);
    }

    for (const obsolete of existing.values()) obsolete.remove();
    ordered.forEach((card, index) => {
      const current = container.children[index];
      if (current !== card) container.insertBefore(card, current || null);
    });
  }

  return { clientModuleSummary, reconcileClientCards };
}));
