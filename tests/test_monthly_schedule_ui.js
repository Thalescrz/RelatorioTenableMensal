"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");

const helpers = require(path.join(
  __dirname,
  "..",
  "src",
  "tenable_reports",
  "webapp",
  "static",
  "monthly_schedule.js",
));

test("prefers the generic scheduler payload", () => {
  const view = helpers.scheduleView({
    scheduler: { backend: "SYSTEMD_USER_TIMER", status: "SYNCHRONIZED" },
    windows_task: { backend: "WINDOWS_TASK_SCHEDULER", status: "DIVERGENT" },
  });

  assert.equal(view.taskLabel, "Sincronizada");
  assert.equal(view.backend, "SYSTEMD_USER_TIMER");
});

test("accepts the legacy windows task payload during transition", () => {
  const view = helpers.scheduleView({
    windows_task: { status: "DIVERGENT" },
  });

  assert.equal(view.taskLabel, "Divergente");
  assert.equal(view.backend, "WINDOWS_TASK_SCHEDULER");
});

test("describes an unavailable backend", () => {
  const view = helpers.scheduleView({
    scheduler: { backend: "SYSTEMD_USER_TIMER", status: "UNAVAILABLE" },
  });

  assert.equal(view.taskLabel, "Indisponível");
});

test("restores the validation button after success, failure, and timeout", async () => {
  const button = { disabled: false };

  const success = await helpers.runWithDisabledButton(button, async () => "ok");
  assert.equal(success, "ok");
  assert.equal(button.disabled, false);

  await assert.rejects(
    helpers.runWithDisabledButton(button, async () => {
      throw new Error("failure");
    }),
    /failure/,
  );
  assert.equal(button.disabled, false);

  await assert.rejects(
    helpers.runWithDisabledButton(button, () => new Promise((resolve, reject) => {
      setTimeout(() => reject(new Error("timeout")), 1);
    })),
    /timeout/,
  );
  assert.equal(button.disabled, false);
});

test("distinguishes policy save, validation, synchronization, and activation", () => {
  assert.match(helpers.scheduleActionMessage("save"), /política foi salva/i);
  assert.match(helpers.scheduleActionMessage("validate"), /nenhuma tarefa/i);
  assert.match(helpers.scheduleActionMessage("apply"), /criada ou atualizada/i);
  assert.match(helpers.scheduleActionMessage("enable"), /ativada/i);
  assert.match(helpers.scheduleActionMessage("disable"), /desativada/i);
});

const clients = [
  { client_id: "a", display_name: "Cliente A", enabled: true, credentials_ready: true },
  { client_id: "b", display_name: "Cliente B", enabled: true, credentials_ready: true },
  { client_id: "inactive", display_name: "Cliente Inativo", enabled: false, credentials_ready: true },
  { client_id: "pending", display_name: "Cliente Pendente", enabled: true, credentials_ready: false },
];

test("monthly selection preserves legacy all and explicitly empty selections", () => {
  const all = helpers.monthlyClientSelectionView({}, clients);
  assert.equal(all.mode, "all");
  assert.ok(all.clients.every(client => client.selected));
  const empty = helpers.monthlyClientSelectionView({ selected_client_ids: [] }, clients);
  assert.equal(empty.mode, "selected");
  assert.ok(empty.clients.every(client => !client.selected));
});

test("monthly selection marks only saved clients and explains ineligible ones", () => {
  const view = helpers.monthlyClientSelectionView({ selected_client_ids: ["b", "inactive"] }, clients);
  assert.deepEqual(view.clients.filter(client => client.selected).map(client => client.client_id), ["b", "inactive"]);
  assert.equal(view.clients[0].display_name, "Cliente A");
  assert.equal(view.clients[2].reason, "Cliente inativo");
  assert.equal(view.clients[3].reason, "Credenciais pendentes");
});

test("monthly selection serializes all, selected and empty without widening scope", () => {
  assert.equal(helpers.monthlyClientSelectionValue("all", ["a"]), null);
  assert.deepEqual(helpers.monthlyClientSelectionValue("selected", ["b", "b"]), ["b"]);
  assert.deepEqual(helpers.monthlyClientSelectionValue("selected", []), []);
});

test("removed saved clients do not turn an unchanged selection into a dirty draft", () => {
  assert.deepEqual(helpers.monthlyClientSelectionPatch(["a", "removed"], ["a"], ["a", "b"]), {});
  assert.deepEqual(helpers.monthlyClientSelectionPatch(["a", "removed"], [], ["a", "b"]), {selected_client_ids: []});
});

test("unchanged monthly selections are omitted from PATCH to preserve concurrent policy edits", () => {
  assert.deepEqual(helpers.monthlyClientSelectionPatch(null, null, ["a", "b"]), {});
  assert.deepEqual(helpers.monthlyClientSelectionPatch(["b", "a"], ["a", "b"], ["a", "b"]), {});
  assert.deepEqual(helpers.monthlyClientSelectionPatch(null, ["a"], ["a", "b"]), {selected_client_ids: ["a"]});
  assert.deepEqual(helpers.monthlyClientSelectionPatch(["a"], null, ["a", "b"]), {selected_client_ids: null});
});
