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
