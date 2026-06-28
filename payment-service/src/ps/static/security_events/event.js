const API_BASE = "/api/ui/security-events";

const subtitle = document.getElementById("event-id-subtitle");
const errorEl = document.getElementById("detail-error");
const summaryEl = document.getElementById("detail-summary");
const jsonSection = document.getElementById("detail-json-section");
const jsonEl = document.getElementById("detail-json");
const copyBtn = document.getElementById("copy-json-btn");

function eventIdFromPath() {
  const parts = window.location.pathname.split("/").filter(Boolean);
  const eventsIndex = parts.indexOf("events");
  if (eventsIndex >= 0 && parts.length > eventsIndex + 1) {
    return decodeURIComponent(parts.slice(eventsIndex + 1).join("/"));
  }
  return null;
}

function formatTime(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value || "—";
  }
  return date.toISOString().replace("T", " ").replace(".000Z", "Z");
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

function field(label, value) {
  const display = value === null || value === undefined || value === "" ? "—" : value;
  return `
    <div class="detail-field">
      <dt>${label}</dt>
      <dd class="mono">${display}</dd>
    </div>
  `;
}

function renderSummary(event) {
  const actor = event.actor || {};
  const resource = event.resource || {};
  const ctx = event.event || {};
  const source = event.source || {};
  const amount =
    resource.amount != null
      ? `${resource.amount} ${resource.currency || ""}`.trim()
      : "";

  summaryEl.innerHTML = `
    <div class="detail-card">
      <div class="detail-card-header">
        ${badge(event.severity)}
        ${outcomeBadge(ctx.outcome)}
        <span class="detail-action mono">${ctx.action || "—"}</span>
      </div>
      <p class="detail-message">${event.message || ""}</p>
      <dl class="detail-grid">
        ${field("Timestamp (UTC)", formatTime(event.timestamp))}
        ${field("Event ID", event.event_id)}
        ${field("Category", (ctx.category || []).join(", "))}
        ${field("Event type", (ctx.type || []).join(", "))}
        ${field("Reason", ctx.reason)}
        ${field("Actor", actor.user_id)}
        ${field("Title", actor.title)}
        ${field("Roles", (actor.roles || []).join(", "))}
        ${field("LOB", actor.lob || resource.owning_lob)}
        ${field("Supervisor", actor.supervisor_id)}
        ${field("Payment ID", resource.id)}
        ${field("Instruction ID", resource.instruction_id)}
        ${field("Resource type", resource.type)}
        ${field("Payment status", resource.status)}
        ${field("Owning LOB", resource.owning_lob)}
        ${field("Amount", amount)}
        ${field("Application", source.application)}
        ${field("Service", source.service)}
        ${field("Source version", source.version)}
      </dl>
    </div>
  `;
}

function showError(message) {
  errorEl.textContent = message;
  errorEl.classList.remove("hidden");
  summaryEl.classList.add("hidden");
  jsonSection.classList.add("hidden");
}

async function loadEvent() {
  const eventId = eventIdFromPath();
  if (!eventId) {
    showError("Missing event id in URL.");
    subtitle.textContent = "Invalid URL";
    return;
  }

  subtitle.textContent = eventId;
  document.title = `Payment Security Event · ${eventId}`;

  if (!AdminAuth.loadSession()) {
    showError("Admin sign-in required.");
    return;
  }

  try {
    const response = await AdminAuth.adminFetch(
      `${API_BASE}/${encodeURIComponent(eventId)}`
    );
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      throw new Error(payload.detail || `HTTP ${response.status}`);
    }
    const payload = await response.json();
    const event = payload.event;
    errorEl.classList.add("hidden");
    renderSummary(event);
    summaryEl.classList.remove("hidden");
    jsonEl.textContent = JSON.stringify(event, null, 2);
    jsonSection.classList.remove("hidden");
  } catch (error) {
    showError(`Could not load event: ${error.message}`);
  }
}

copyBtn.addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText(jsonEl.textContent);
    copyBtn.textContent = "Copied";
    window.setTimeout(() => {
      copyBtn.textContent = "Copy JSON";
    }, 1500);
  } catch {
    copyBtn.textContent = "Copy failed";
  }
});

AdminAuth.bindAdminAuthPanel({
  statusEl: document.getElementById("auth-status"),
  userEl: document.getElementById("auth-user"),
  passwordEl: document.getElementById("auth-password"),
  loginBtn: document.getElementById("auth-login-btn"),
  logoutBtn: document.getElementById("auth-logout-btn"),
  onAuthenticated: () => {
    void loadEvent();
  },
});
