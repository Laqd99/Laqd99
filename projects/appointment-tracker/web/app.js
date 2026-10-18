"use strict";

const form = document.querySelector("#appointment-form");
const list = document.querySelector("#appointment-list");
const search = document.querySelector("#search");
const statusFilter = document.querySelector("#status-filter");
const refreshButton = document.querySelector("#refresh-button");
const submitButton = document.querySelector("#submit-button");
const successMessage = document.querySelector("#success-message");
const errorMessage = document.querySelector("#error-message");
const statusLabels = { scheduled: "Scheduled", completed: "Completed", cancelled: "Cancelled" };
let appointments = [];
let loaded = false;
let loading = false;
let submitting = false;
let loadVersion = 0;
const pendingUpdates = new Set();

document.querySelector("#current-date").textContent = new Intl.DateTimeFormat(undefined, {
  weekday: "short", month: "long", day: "numeric", year: "numeric",
}).format(new Date());

function notify(message, error = false) {
  const target = error ? errorMessage : successMessage;
  target.textContent = message;
  target.hidden = false;
}

function clearNotices() {
  successMessage.hidden = true;
  errorMessage.hidden = true;
  successMessage.textContent = "";
  errorMessage.textContent = "";
}

async function request(path, options = {}) {
  if (window.careScheduleDemo) return window.careScheduleDemo.request(path, options);
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 15000);
  try {
    const response = await fetch(path, {
      credentials: "same-origin",
      ...options,
      headers: { Accept: "application/json", ...(options.body ? { "Content-Type": "application/json" } : {}), ...options.headers },
      signal: controller.signal,
    });
    const data = await response.json().catch(() => null);
    if (!response.ok) {
      throw new Error(data && typeof data.error === "string" ? data.error : `The request could not be completed (${response.status}). Please try again.`);
    }
    if (!data) throw new Error("The server returned an unreadable response. Please try again.");
    return data;
  } catch (error) {
    if (error.name === "AbortError") throw new Error("The request took too long. Please refresh to check the latest schedule before trying again.");
    if (error instanceof TypeError) throw new Error("Could not reach the server. Check your connection, then refresh to see the latest schedule.");
    throw error;
  } finally {
    clearTimeout(timeout);
  }
}

// Build a local date from its components so an appointment never shifts time zones.
function localDate(value) {
  const [date, time] = value.split("T");
  const [year, month, day] = date.split("-").map(Number);
  const [hour, minute] = time.split(":").map(Number);
  return new Date(year, month - 1, day, hour, minute);
}

function node(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
}

function appointmentCard(appointment) {
  const date = localDate(appointment.starts_at);
  const card = node("article", "appointment");
  const tile = node("div", "date-tile");
  tile.setAttribute("aria-hidden", "true");
  tile.append(node("span", "", new Intl.DateTimeFormat(undefined, { month: "short" }).format(date)), node("strong", "", String(date.getDate())));

  const content = node("div", "appointment-content");
  const heading = node("h3", "", appointment.patient_alias);
  const details = node("p", "appointment-details");
  const time = node("time", "", new Intl.DateTimeFormat(undefined, {
    weekday: "short", month: "short", day: "numeric", year: "numeric", hour: "numeric", minute: "2-digit",
  }).format(date));
  time.dateTime = appointment.starts_at;
  details.append(time, node("span", "detail-separator", "·"), node("span", "", appointment.clinician));
  content.append(heading, details, node("p", "appointment-reason", appointment.reason));

  const controls = node("div", "status-control");
  const select = node("select", `status-select ${appointment.status}`);
  select.setAttribute("aria-label", `Status for ${appointment.patient_alias}, ${time.textContent}`);
  for (const [value, label] of Object.entries(statusLabels)) {
    const option = node("option", "", label);
    option.value = value;
    select.append(option);
  }
  select.value = appointment.status;
  select.disabled = pendingUpdates.has(appointment.id);
  select.addEventListener("change", () => updateStatus(appointment, select));
  controls.append(select);
  card.append(tile, content, controls);
  return card;
}

function render() {
  if (loaded) {
    document.querySelector("#total-count").textContent = String(appointments.length);
    document.querySelector("#scheduled-count").textContent = String(appointments.filter(item => item.status === "scheduled").length);
    document.querySelector("#completed-count").textContent = String(appointments.filter(item => item.status === "completed").length);
  }
  const query = search.value.trim().toLocaleLowerCase();
  const visible = appointments.filter(item => (
    (statusFilter.value === "all" || item.status === statusFilter.value)
    && [item.patient_alias, item.clinician, item.reason].some(value => value.toLocaleLowerCase().includes(query))
  )).sort((a, b) => a.starts_at.localeCompare(b.starts_at) || a.id - b.id);
  list.replaceChildren(...visible.map(appointmentCard));
  list.setAttribute("aria-busy", String(loading));
  const summary = document.querySelector("#results-summary");
  summary.textContent = loaded
    ? `${visible.length} ${visible.length === 1 ? "appointment" : "appointments"}${query || statusFilter.value !== "all" ? ` of ${appointments.length}` : ""}${loading ? " · Refreshing…" : ""}`
    : loading ? "Loading appointments…" : "Schedule unavailable";
  const empty = document.querySelector("#empty-state");
  empty.hidden = visible.length > 0 || loading;
  if (!loaded) {
    document.querySelector("#empty-title").textContent = "Let’s reconnect.";
    document.querySelector("#empty-copy").textContent = "Refresh to try loading your appointments again.";
  } else if (appointments.length > 0) {
    document.querySelector("#empty-title").textContent = "No appointments found.";
    document.querySelector("#empty-copy").textContent = "Try a different search or choose another status.";
  } else {
    document.querySelector("#empty-title").textContent = "A fresh start.";
    document.querySelector("#empty-copy").textContent = "Add an appointment to start your schedule.";
  }
}

async function loadAppointments() {
  const version = ++loadVersion;
  loading = true;
  refreshButton.disabled = true;
  render();
  try {
    const data = await request("/api/appointments");
    if (version !== loadVersion) return;
    if (!Array.isArray(data.appointments)) throw new Error("The server returned an unexpected schedule. Please refresh to try again.");
    appointments = data.appointments;
    loaded = true;
  } catch (error) {
    if (version === loadVersion) notify(`${loaded ? "Could not refresh the schedule. The last loaded appointments are still shown. " : ""}${error.message}`, true);
  } finally {
    if (version === loadVersion) {
      loading = false;
      refreshButton.disabled = false;
      render();
    }
  }
}

async function updateStatus(appointment, select) {
  if (pendingUpdates.has(appointment.id)) return;
  const status = select.value;
  pendingUpdates.add(appointment.id);
  select.disabled = true;
  clearNotices();
  // Invalidate any older read so it cannot overwrite a successful mutation.
  ++loadVersion;
  try {
    const data = await request(`/api/appointments/${encodeURIComponent(appointment.id)}`, {
      method: "PATCH", body: JSON.stringify({ status }),
    });
    appointments = appointments.map(item => item.id === appointment.id ? data.appointment : item);
    notify(`${appointment.patient_alias}’s appointment is now ${statusLabels[status].toLowerCase()}.`);
  } catch (error) {
    select.value = appointment.status;
    notify(error.message, true);
  } finally {
    pendingUpdates.delete(appointment.id);
    await loadAppointments();
  }
}

form.addEventListener("submit", async event => {
  event.preventDefault();
  if (submitting || !form.reportValidity()) return;
  const payload = Object.fromEntries(new FormData(form));
  for (const field of ["patient_alias", "clinician", "reason"]) {
    payload[field] = payload[field].trim();
    if (!payload[field]) {
      form.elements.namedItem(field).focus();
      notify("Please fill in each appointment detail.", true);
      return;
    }
  }
  submitting = true;
  submitButton.disabled = true;
  submitButton.textContent = "Adding appointment…";
  form.setAttribute("aria-busy", "true");
  clearNotices();
  ++loadVersion;
  try {
    const data = await request("/api/appointments", { method: "POST", body: JSON.stringify(payload) });
    appointments.push(data.appointment);
    loaded = true;
    form.reset();
    search.value = "";
    statusFilter.value = "all";
    notify(`Appointment added for ${data.appointment.patient_alias}.`);
    document.querySelector("#patient-alias").focus();
    await loadAppointments();
  } catch (error) {
    notify(error.message, true);
    // A connection failure can happen after a save; refresh before another submission.
    await loadAppointments();
  } finally {
    submitting = false;
    submitButton.disabled = false;
    submitButton.textContent = "+ Add appointment";
    form.setAttribute("aria-busy", "false");
  }
});

search.addEventListener("input", render);
statusFilter.addEventListener("change", render);
refreshButton.addEventListener("click", () => {
  clearNotices();
  loadAppointments();
});
loadAppointments();
