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
let source = null;

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

function resourceCell(resource = {}) {
  return `
    <div class="mono">${resource.id || "—"}</div>
    <div class="muted">${resource.type || ""}${resource.status ? ` · ${resource.status}` : ""}</div>
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

function shortEventId(eventId) {
  if (!eventId) {
    return "—";
  }
  const parts = String(eventId).split("-");
  return parts.length > 1 ? parts[parts.length - 1] : eventId;
}

function eventIdLink(eventId) {
  if (!eventId) {
    return "—";
  }
  const href = `/ui/security-events/events/${encodeURIComponent(eventId)}`;
  const label = shortEventId(eventId);
  return `<a class="event-id-link mono" href="${href}" title="${eventId}">${label}</a>`;
}

function renderTable() {
  tbody.innerHTML = "";
  const visible = events.filter(passesFilters);
  emptyState.classList.toggle("hidden", visible.length > 0);

  visible.forEach((event, index) => {
    const row = document.createElement("tr");
    row.className = severityClass(event.severity);
    if (index === 0) {
      row.classList.add("row-new");
    }
    row.innerHTML = `
      <td class="col-event-id">${eventIdLink(event.event_id)}</td>
      <td class="mono">${formatTime(event.timestamp)}</td>
      <td>${badge(event.severity)}</td>
      <td class="mono">${event?.event?.action || "—"}</td>
      <td>${outcomeBadge(event?.event?.outcome)}</td>
      <td>${actorCell(event.actor)}</td>
      <td class="mono">${event?.actor?.lob || "—"}</td>
      <td>${resourceCell(event.resource)}</td>
      <td class="message">${event.message || ""}</td>
    `;
    tbody.appendChild(row);
  });
  updateStats();
}

function prependEvent(event, { isLive = false } = {}) {
  if (!event?.event_id || seenIds.has(event.event_id)) {
    return;
  }
  seenIds.add(event.event_id);
  events.unshift(event);
  if (events.length > MAX_ROWS) {
    const removed = events.pop();
    if (removed?.event_id) {
      seenIds.delete(removed.event_id);
    }
  }
  updateActionFilterOptions();
  renderTable();
  if (isLive) {
    const firstRow = tbody.querySelector("tr");
    if (firstRow) {
      firstRow.classList.add("row-new");
      window.setTimeout(() => firstRow.classList.remove("row-new"), 1200);
    }
  }
}

async function loadInitialEvents() {
  const response = await fetch(API_BASE);
  const payload = await response.json();
  const initial = (payload.events || []).slice().reverse();
  initial.forEach((event) => prependEvent(event));
}

function setConnectionStatus(state, label) {
  connectionStatus.className = `status-pill status-${state}`;
  connectionStatus.textContent = label;
}

function connectStream() {
  if (source) {
    source.close();
  }
  setConnectionStatus("connecting", "Connecting");
  source = new EventSource(`${API_BASE}/stream`);

  source.addEventListener("connected", () => {
    setConnectionStatus("live", "Live · change stream");
  });

  source.onmessage = (message) => {
    if (paused) {
      return;
    }
    try {
      const event = JSON.parse(message.data);
      prependEvent(event, { isLive: true });
    } catch (error) {
      console.error("invalid SSE payload", error);
    }
  };

  source.onerror = () => {
    setConnectionStatus("error", "Reconnecting…");
    source.close();
    window.setTimeout(connectStream, 2000);
  };
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

loadInitialEvents().then(connectStream);
