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
  "report_period.js",
));

test("builds distinct calendar and rolling requests", () => {
  assert.deepEqual(
    helpers.periodRequest("calendar", {}),
    { mode: "manual", previous_calendar_month: true },
  );
  assert.deepEqual(
    helpers.periodRequest("rolling", {}),
    { mode: "manual" },
  );
});

test("formats competence, timezone, and exclusive boundaries", () => {
  const text = helpers.formatPeriodPreview({
    periods: [{
      period_id: "2026-10",
      timezone: "America/Fortaleza",
      start_at: "2026-10-01T03:00:00Z",
      end_at: "2026-11-01T03:00:00Z",
      interval: "[start_at, end_at)",
    }],
  });

  assert.match(text, /2026-10/);
  assert.match(text, /America\/Fortaleza/);
  assert.match(text, /01\/10\/2026/);
  assert.match(text, /fim exclusivo/i);
});
