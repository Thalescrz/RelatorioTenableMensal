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
  "environment_status.js",
));

function payload() {
  return {
    status: "WARNING",
    official_publication_ready: true,
    platform: { id: "linux", system: "Linux", architecture: "x86_64", status: "READY" },
    font_profile: { id: "linux-carlito-v1", status: "READY" },
    libreoffice: { version: "26.2.4.2", source: "portable", executable: "soffice", status: "READY" },
    postgresql: { configured: true, status: "READY" },
    storage: { roots_checked: 2, status: "READY" },
    timezone: { status: "READY" },
    scheduler: { backend: "SYSTEMD_USER_TIMER", state: "MISSING", enabled: false, status: "WARNING" },
    issues: [{ code: "SCHEDULER_UNAVAILABLE", component: "scheduler", status: "WARNING", message: "Requer atenção.", remediation: "Revise o serviço." }],
  };
}

test("maps ready, warning and blocked states to operational labels", () => {
  const view = helpers.environmentView(payload());

  assert.equal(view.overallLabel, "Atenção");
  assert.equal(view.publicationLabel, "Publicação disponível");
  assert.equal(view.components.find(item => item.key === "platform").detail, "Linux · x86_64");
  assert.equal(view.components.find(item => item.key === "scheduler").statusLabel, "Atenção");
  assert.equal(view.issues[0].code, "SCHEDULER_UNAVAILABLE");

  const blocked = helpers.environmentView({ ...payload(), status: "BLOCKED", official_publication_ready: false });
  assert.equal(blocked.overallLabel, "Bloqueado");
  assert.equal(blocked.publicationLabel, "Publicação bloqueada");
});

test("uses neutral placeholders for absent optional details", () => {
  const view = helpers.environmentView({
    ...payload(),
    libreoffice: { status: "BLOCKED", version: null, source: null, executable: null },
    font_profile: { status: "BLOCKED", id: null },
  });

  assert.equal(view.components.find(item => item.key === "libreoffice").detail, "Não detectado");
  assert.equal(view.components.find(item => item.key === "font_profile").detail, "Não detectado");
});
