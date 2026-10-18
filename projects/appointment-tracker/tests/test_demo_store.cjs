"use strict";

const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { join } = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const source = readFileSync(join(__dirname, "..", "web", "demo-store.js"), "utf8");
const storageKey = "care-schedule.demo.v1";

// Only the storage notice and demo badge need a DOM for these transport tests.
function createDemo({ saved = new Map(), denied = false, quota = false } = {}) {
  const elements = new Map();
  const element = () => ({
    textContent: "",
    after(child) { elements.set(child.id, child); },
    setAttribute() {},
    replaceChildren() {},
  });
  const storage = {
    getItem(key) {
      if (denied) throw new Error("Storage access denied");
      return saved.has(key) ? saved.get(key) : null;
    },
    setItem(key, value) {
      if (quota) throw new Error("Storage quota exceeded");
      saved.set(key, value);
    },
  };
  const context = {
    window: { localStorage: storage },
    document: {
      createElement: element,
      createTextNode: textContent => ({ textContent }),
      querySelector(selector) {
        if (!elements.has(selector)) elements.set(selector, element());
        return elements.get(selector);
      },
    },
  };
  vm.runInNewContext(source, context, { filename: "demo-store.js" });
  const request = context.window.careScheduleDemo.request;
  return {
    saved,
    request,
    list: async () => (await request("/api/appointments")).appointments,
    create: async payload => (await request("/api/appointments", {
      method: "POST", body: JSON.stringify(payload),
    })).appointment,
    status: async (id, status) => (await request(`/api/appointments/${id}`, {
      method: "PATCH", body: JSON.stringify({ status }),
    })).appointment,
    notice: () => elements.get("demo-storage-notice").textContent,
  };
}

function booking(overrides = {}) {
  return {
    patient_alias: "Fictional Test",
    clinician: "Dr. Test Clinician",
    starts_at: "2032-05-20T09:30",
    reason: "Fictional follow-up",
    ...overrides,
  };
}

const hasStatus = status => error => error.status === status;

test("scheduled slots reject conflicts, allow cancelled reuse, and reject conflicting restoration", async () => {
  const demo = createDemo();
  const first = await demo.create(booking());
  await assert.rejects(demo.create(booking({ patient_alias: "Fictional Other" })), hasStatus(409));

  assert.equal((await demo.status(first.id, "cancelled")).status, "cancelled");
  const replacement = await demo.create(booking({ patient_alias: "Fictional Replacement" }));
  assert.notEqual(first.id, replacement.id);
  await assert.rejects(demo.status(first.id, "scheduled"), hasStatus(409));

  const remaining = await demo.list();
  assert.equal(remaining.find(item => item.id === first.id).status, "cancelled");
  assert.equal(remaining.find(item => item.id === replacement.id).status, "scheduled");
});

test("new appointments and status changes survive a fresh demo instance", async () => {
  const saved = new Map();
  const first = createDemo({ saved });
  const initial = await first.list();
  assert.equal(initial.length, 4);
  const added = await first.create(booking());
  await first.status(added.id, "completed");

  const reopened = createDemo({ saved });
  const restored = await reopened.list();
  assert.equal(restored.length, initial.length + 1);
  assert.equal(restored.find(item => item.id === added.id).status, "completed");
  const next = await reopened.create(booking({ starts_at: "2032-05-20T10:30" }));
  assert.ok(next.id > added.id);
  assert.match(reopened.notice(), /Saved in this browser/);
});

test("appointment validation rejects invalid fields and calendar dates while accepting a leap day", async () => {
  const demo = createDemo();
  const invalid = [
    booking({ patient_alias: "   " }),
    booking({ clinician: "a".repeat(61) }),
    booking({ reason: "a".repeat(241) }),
    booking({ reason: null }),
    booking({ extra: "unsupported" }),
    booking({ starts_at: "2025-02-29T10:00" }),
    booking({ starts_at: "2024-02-30T10:00" }),
    booking({ starts_at: "2100-02-29T10:00" }),
    booking({ starts_at: "2024-01-01T24:00" }),
    booking({ starts_at: "2024-01-01T10:60" }),
    booking({ starts_at: "0000-01-01T10:00" }),
    booking({ starts_at: "2032-05-20T09:30Z" }),
  ];
  for (const payload of invalid) {
    await assert.rejects(demo.create(payload), hasStatus(400), JSON.stringify(payload));
  }
  assert.equal((await demo.list()).length, 4);

  const leap = await demo.create(booking({ patient_alias: "  Fictional Leap  ", starts_at: "2024-02-29T10:00" }));
  assert.equal(leap.patient_alias, "Fictional Leap");
  assert.equal(leap.starts_at, "2024-02-29T10:00");
  await assert.rejects(demo.status(leap.id, "unknown"), hasStatus(400));
  await assert.rejects(demo.status(999999, "completed"), hasStatus(404));
});

test("malformed or invalid saved data stays intact while the demo remains usable in memory", async t => {
  for (const value of ["{broken", JSON.stringify({ version: 1, nextId: 1, appointments: [{ id: 1 }] })]) {
    await t.test(value.startsWith("{broken") ? "malformed JSON" : "invalid saved schema", async () => {
      const saved = new Map([[storageKey, value]]);
      const demo = createDemo({ saved });
      assert.equal((await demo.list()).length, 4);
      await demo.create(booking());
      assert.equal((await demo.list()).length, 5);
      assert.equal(saved.get(storageKey), value);
      assert.match(demo.notice(), /Saved demo data could not be read/);
      assert.match(demo.notice(), /page closes or reloads/);
    });
  }
});

test("denied storage and quota failures retain edits for the current page and explain persistence", async t => {
  for (const [name, options] of [["denied storage", { denied: true }], ["storage quota", { quota: true }]]) {
    await t.test(name, async () => {
      const demo = createDemo(options);
      const added = await demo.create(booking());
      await demo.status(added.id, "completed");
      const appointments = await demo.list();
      assert.equal(appointments.length, 5);
      assert.equal(appointments.find(item => item.id === added.id).status, "completed");
      assert.match(demo.notice(), /storage.*unavailable/i);
      assert.match(demo.notice(), /page closes or reloads/);
    });
  }
});
