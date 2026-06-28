const API_BASE = "/api/ui/security-events";
const MAX_ROWS = 500;
const seenIds = new Set();
const events = [];

const tbody = document.getElementById("events-body");
const emptyState = document.getElementById("empty-state");
const connectionStatus = document.getElementById("connection-status");
const statTotal = document.getElementById("stat-total");
const statInfo = document.getElementById("stat-info");
const statAlert = document.getElementById("stat-alert");
const severityFilter = document.getElementById("severity-filter");
const actionFilter = document.getElementById("action-filter");
const pauseBtn = document.getElementById("pause-btn");
const clearBtn = document.getElementById("clear-btn");

let paused = false;
let pollTimer = null;

function formatTime(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value || "—";
  }
  return date.toISOString().replace("T", " ").replace(".000Z", "Z");
}

function severityClass(severity) {
  return `sev-${severity || "INFO"}`;
}

function badge(severity) {
  const level = severity || "INFO";
  return `<span class="badge badge-${level}">${level}</span>`;
}

function outcomeBadge(outcome) {
  const normalized = (outcome || "").toLowerCase();
  const css = normalized === "failure" ? "outcome-failure" : "outcome-success";
  return `<span class="outcome ${css}">${outcome || "—"}</span>`;
}

function actorCell(actor = {}) {
  const roles = (actor.roles || []).join(", ");
  return `
    <div class="mono">${actor.user_id || "—"}</div>
    <div class="muted">${actor.title || ""}${roles ? ` · ${roles}` : ""}</div>
  `;
}

function paymentCell(resource = {}) {
  const amount = resource.amount != null ? `${resource.amount} ${resource.currency || ""}`.trim() : "";
  return `
    <div class="mono">${resource.id || "—"}</div>
    <div class="muted">${resource.status || ""}${amount ? ` · ${amount}` : ""}</div>
  `;
}

function passesFilters(event) {
  const severity = severityFilter.value;
  const action = actionFilter.value;
  const eventAction = event?.event?.action || "";
  if (severity !== "ALL" && event.severity !== severity) {
    return false;
  }
  if (action !== "ALL" && eventAction !== action) {
    return false;
  }
  return true;
}

function updateStats() {
  const visible = events.filter(passesFilters);
  statTotal.textContent = String(visible.length);
  statInfo.textContent = String(visible.filter((event) => event.severity === "INFO").length);
  statAlert.textContent = String(visible.filter((event) => event.severity === "ALERT").length);
}

function updateActionFilterOptions() {
  const actions = new Set(events.map((event) => event?.event?.action).filter(Boolean));
  const current = actionFilter.value;
  actionFilter.innerHTML = '<option value="ALL">All</option>';
  [...actions].sort().forEach((action) => {
    const option = document.createElement("option");
    option.value = action;
    option.textContent = action;
    actionFilter.appendChild(option);
  });
  if ([...actions, "ALL"].includes(current)) {
    actionFilter.value = current;
  }
}

function eventIdLink(eventId) {
  if (!eventId) {
    return "—";
  }
  const href = `/ui/security-events/events/${encodeURIComponent(eventId)}`;
  return `<a class="event-id-link mono" href="${href}">${eventId}</a>`;
}

function renderTable() {
  tbody.innerHTML = "";
  const visible = events.filter(passesFilters);
  emptyState.classList.toggle("hidden", visible.length > 0);

  visible.forEach((event) => {
    const row = document.createElement("tr");
    row.className = severityClass(event.severity);
    row.innerHTML = `
      <td class="col-event-id">${eventIdLink(event.event_id)}</td>
      <td class="mono">${formatTime(event.timestamp)}</td>
      <td>${badge(event.severity)}</td>
      <td class="mono">${event?.event?.action || "—"}</td>
      <td>${outcomeBadge(event?.event?.outcome)}</td>
      <td>${actorCell(event.actor)}</td>
      <td class="mono">${event?.actor?.lob || event?.resource?.owning_lob || "—"}</td>
      <td>${paymentCell(event.resource)}</td>
      <td class="message">${event.message || ""}</td>
    `;
    tbody.appendChild(row);
  });
  updateStats();
}

function replaceEvents(nextEvents) {
  events.length = 0;
  seenIds.clear();
  nextEvents.forEach((event) => {
    if (!event?.event_id || seenIds.has(event.event_id)) {
      return;
    }
    seenIds.add(event.event_id);
    events.push(event);
  });
  updateActionFilterOptions();
  renderTable();
}

async function loadInitialEvents() {
  if (!AdminAuth.loadSession()) {
    setConnectionStatus("error", "Sign in required");
    return;
  }
  setConnectionStatus("connecting", "Loading");
  const response = await AdminAuth.adminFetch(API_BASE);
  const payload = await response.json();
  replaceEvents((payload.events || []).slice(0, MAX_ROWS));
  setConnectionStatus("live", `Loaded ${events.length}`);
}

function startPolling() {
  if (pollTimer) {
    clearInterval(pollTimer);
  }
  pollTimer = setInterval(() => {
    if (!paused) {
      void loadInitialEvents();
    }
  }, 2000);
}

function setConnectionStatus(state, label) {
  connectionStatus.className = `status-pill status-${state}`;
  connectionStatus.textContent = label;
}

severityFilter.addEventListener("change", renderTable);
actionFilter.addEventListener("change", renderTable);

pauseBtn.addEventListener("click", () => {
  paused = !paused;
  pauseBtn.textContent = paused ? "Resume live feed" : "Pause live feed";
});

clearBtn.addEventListener("click", () => {
  events.length = 0;
  seenIds.clear();
  renderTable();
});

AdminAuth.bindAdminAuthPanel({
  statusEl: document.getElementById("auth-status"),
  userEl: document.getElementById("auth-user"),
  passwordEl: document.getElementById("auth-password"),
  loginBtn: document.getElementById("auth-login-btn"),
  logoutBtn: document.getElementById("auth-logout-btn"),
  onAuthenticated: () => {
    void loadInitialEvents();
    startPolling();
  },
});
