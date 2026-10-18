"use strict";

// This adapter is included only in the standalone browser demo.
// The Python application continues to use its HTTP API and SQLite database.
(() => {
  const STORAGE_KEY = "care-schedule.demo.v1";
  const statuses = new Set(["scheduled", "completed", "cancelled"]);
  let persistent = true;
  let storage;
  let state = seedState();
  let memoryReason = "";

  const notice = document.createElement("p");
  notice.id = "demo-storage-notice";
  notice.className = "intro-copy";
  notice.setAttribute("role", "status");
  notice.setAttribute("aria-live", "polite");
  document.querySelector(".intro-copy").after(notice);
  const label = document.querySelector(".demo-label");
  const indicator = document.createElement("span");
  indicator.setAttribute("aria-hidden", "true");
  label.replaceChildren(indicator, document.createTextNode("Fictional data · browser demo"));

  function updateNotice() {
    notice.textContent = persistent
      ? "Saved in this browser. This demo works without a server."
      : `${memoryReason} Changes last only until this page closes or reloads.`;
  }

  function useMemory(reason) {
    persistent = false;
    memoryReason = reason;
    updateNotice();
  }

  function error(status, message) {
    const result = new Error(message);
    result.status = status;
    return result;
  }

  function copy(value) {
    return JSON.parse(JSON.stringify(value));
  }

  function exactKeys(value, expected) {
    return value !== null && typeof value === "object" && !Array.isArray(value)
      && Object.keys(value).length === expected.length
      && expected.every(key => Object.prototype.hasOwnProperty.call(value, key));
  }

  function validDateTime(value) {
    if (typeof value !== "string" || !/^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}$/.test(value)) return false;
    const [year, month, day, hour, minute] = value.split(/[-T:]/).map(Number);
    if (year < 1 || month < 1 || month > 12 || day < 1 || hour > 23 || minute > 59) return false;
    const leapYear = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
    const daysInMonth = [31, leapYear ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
    return day <= daysInMonth[month - 1];
  }

  function validateAppointment(payload) {
    if (!exactKeys(payload, ["patient_alias", "clinician", "starts_at", "reason"])) {
      throw error(400, "Provide patient_alias, clinician, starts_at, and reason only.");
    }
    const result = {};
    for (const [field, limit] of [["patient_alias", 60], ["clinician", 60], ["reason", 240]]) {
      const value = payload[field];
      const length = typeof value === "string" ? Array.from(value.trim()).length : 0;
      if (length < 1 || length > limit) throw error(400, `${field} must contain between 1 and ${limit} characters.`);
      result[field] = value.trim();
    }
    if (!validDateTime(payload.starts_at)) {
      throw error(400, "starts_at must be a valid date and time in YYYY-MM-DDTHH:MM format.");
    }
    result.starts_at = payload.starts_at;
    return result;
  }

  function seedState() {
    const examples = [
      ["Fictional Amber", "Dr. Demo Rowan", -1, "09:00", "Demo follow-up", "completed"],
      ["Fictional Birch", "Dr. Demo Rowan", 0, "14:00", "Demo wellbeing check-in", "scheduled"],
      ["Fictional Cedar", "Dr. Demo Morgan", 1, "10:30", "Demo routine appointment", "scheduled"],
      ["Fictional Dune", "Dr. Demo Morgan", 2, "11:00", "Demo consultation", "cancelled"],
    ];
    const today = new Date();
    return {
      version: 1,
      nextId: 5,
      appointments: examples.map(([patient_alias, clinician, offset, time, reason, status], index) => {
        const day = new Date(today.getFullYear(), today.getMonth(), today.getDate() + offset);
        const localDay = [day.getFullYear(), String(day.getMonth() + 1).padStart(2, "0"), String(day.getDate()).padStart(2, "0")].join("-");
        return { id: index + 1, patient_alias, clinician, starts_at: `${localDay}T${time}`, reason, status };
      }),
    };
  }

  function validateState(value) {
    if (!exactKeys(value, ["version", "nextId", "appointments"]) || value.version !== 1
      || !Number.isSafeInteger(value.nextId) || value.nextId < 1 || !Array.isArray(value.appointments)) {
      throw new Error("Invalid demo data.");
    }
    const ids = new Set();
    const reserved = new Map();
    for (const item of value.appointments) {
      if (!exactKeys(item, ["id", "patient_alias", "clinician", "starts_at", "reason", "status"])
        || !Number.isSafeInteger(item.id) || item.id < 1 || item.id >= value.nextId
        || ids.has(item.id) || !statuses.has(item.status)) throw new Error("Invalid appointment.");
      const fields = validateAppointment({ patient_alias: item.patient_alias, clinician: item.clinician, starts_at: item.starts_at, reason: item.reason });
      if (Object.entries(fields).some(([key, field]) => item[key] !== field)) throw new Error("Invalid appointment.");
      ids.add(item.id);
      if (item.status === "scheduled") {
        if (!reserved.has(item.clinician)) reserved.set(item.clinician, new Set());
        const times = reserved.get(item.clinician);
        if (times.has(item.starts_at)) throw new Error("Conflicting appointments.");
        times.add(item.starts_at);
      }
    }
    return value;
  }

  function writeState(nextState) {
    state = nextState;
    if (!persistent) return;
    try {
      storage.setItem(STORAGE_KEY, JSON.stringify(state));
    } catch {
      useMemory("Browser storage is full or unavailable.");
    }
  }

  function readState() {
    if (!persistent) return state;
    let saved;
    try {
      saved = storage.getItem(STORAGE_KEY);
    } catch {
      useMemory("Browser storage is unavailable.");
      return state;
    }
    if (saved === null) {
      writeState(seedState());
      return state;
    }
    try {
      state = validateState(JSON.parse(saved));
    } catch {
      // Leave the original saved value intact rather than destroying unreadable data.
      useMemory("Saved demo data could not be read. Showing the last valid schedule or sample appointments.");
    }
    return state;
  }

  function payloadFrom(options) {
    try {
      const payload = JSON.parse(options.body);
      if (payload === null || typeof payload !== "object" || Array.isArray(payload)) throw new Error();
      return payload;
    } catch {
      throw error(400, "Send a complete, valid JSON object.");
    }
  }

  function checkConflict(appointment, items, ownId) {
    if (appointment.status === "scheduled" && items.some(item => item.id !== ownId
      && item.status === "scheduled" && item.clinician === appointment.clinician && item.starts_at === appointment.starts_at)) {
      throw error(409, "This clinician already has a scheduled appointment at that time.");
    }
  }

  async function request(path, options = {}) {
    const current = readState();
    const method = (options.method || "GET").toUpperCase();
    if (method === "GET" && path === "/api/appointments") {
      return { appointments: copy(current.appointments).sort((a, b) => a.starts_at.localeCompare(b.starts_at) || a.id - b.id) };
    }
    if (method === "POST" && path === "/api/appointments") {
      const fields = validateAppointment(payloadFrom(options));
      if (current.nextId >= Number.MAX_SAFE_INTEGER) throw error(503, "The demo cannot allocate another appointment ID.");
      const appointment = { id: current.nextId, ...fields, status: "scheduled" };
      checkConflict(appointment, current.appointments);
      writeState({ version: 1, nextId: current.nextId + 1, appointments: [...current.appointments, appointment] });
      return { appointment: copy(appointment) };
    }
    if (method === "PATCH" && /^\/api\/appointments\/[0-9]+$/.test(path)) {
      const payload = payloadFrom(options);
      if (!exactKeys(payload, ["status"]) || !statuses.has(payload.status)) {
        throw error(400, "Provide only status: scheduled, completed, or cancelled.");
      }
      const id = Number(path.slice(path.lastIndexOf("/") + 1));
      const original = current.appointments.find(item => item.id === id);
      if (!original) throw error(404, "This appointment does not exist.");
      const appointment = { ...original, status: payload.status };
      checkConflict(appointment, current.appointments, id);
      writeState({ ...current, appointments: current.appointments.map(item => item.id === id ? appointment : item) });
      return { appointment: copy(appointment) };
    }
    throw error(["GET", "POST", "PATCH"].includes(method) ? 404 : 405, "This page or endpoint does not exist.");
  }

  try {
    storage = window.localStorage;
    readState();
  } catch {
    useMemory("Browser storage is unavailable.");
  }
  updateNotice();
  window.careScheduleDemo = Object.freeze({ request });
})();
