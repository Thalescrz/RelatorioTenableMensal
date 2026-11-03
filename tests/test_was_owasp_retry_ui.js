"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const source = fs.readFileSync(path.join(
  __dirname,
  "..",
  "src",
  "tenable_reports",
  "webapp",
  "static",
  "app.js",
), "utf8");

test("WAS retry exposes the current permanent OWASP mode", () => {
  assert.match(source, /Tentar somente WAS novamente/);
  assert.match(source, /Modo OWASP 2025 atual/);
  assert.match(source, /Inclusivo — qualquer associação com a categoria/);
  assert.match(source, /Exclusivo — mesma seleção da console Tenable/);
});

test("WAS retry uses only the isolated recovery route", () => {
  assert.match(source, /\/api\/was-recoveries\/\$\{encodeURIComponent\(runId\)\}\/retry/);
  assert.match(source, /VM, assets, TAG e Cloud não serão repetidos/);
});
