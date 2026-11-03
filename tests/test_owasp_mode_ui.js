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
  "owasp_mode.js",
));

test("defaults legacy clients to inclusive mode", () => {
  assert.deepEqual(helpers.owaspModeView({ wasEnabled: true }), {
    value: "inclusive",
    disabled: false,
  });
});

test("preserves the saved exclusive mode while WAS is disabled", () => {
  assert.deepEqual(helpers.owaspModeView({
    wasEnabled: false,
    savedMode: "console_exclusive",
  }), {
    value: "console_exclusive",
    disabled: true,
  });
});

test("normalizes an unknown saved mode to the safe inclusive default", () => {
  assert.equal(helpers.normalizeOwaspMode("automatic"), "inclusive");
});
