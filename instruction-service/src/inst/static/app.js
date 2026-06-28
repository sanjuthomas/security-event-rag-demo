const MAX_ROWS = 500;
const tbody = document.getElementById("instructions-body");
const emptyState = document.getElementById("empty-state");
const loadStatus = document.getElementById("load-status");
const statTotal = document.getElementById("stat-total");
const statusFilter = document.getElementById("status-filter");
const statusHelpBtn = document.getElementById("status-help-btn");
const statusHelpPanel = document.getElementById("status-help-panel");
const lobFilter = document.getElementById("lob-filter");
const refreshBtn = document.getElementById("refresh-btn");
const pauseBtn = document.getElementById("pause-btn");
const clearBtn = document.getElementById("clear-btn");

let instructions = [];
let paused = false;
let pollTimer = null;

function formatTime(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value || "—";
  }
  return date.toISOString().replace("T", " ").replace(".000Z", "Z");
}

function instructionIdLink(instructionId) {
  if (!instructionId) {
    return "—";
  }
  const href = `/ui/instructions/${encodeURIComponent(instructionId)}`;
  return `<a class="id-link mono" href="${href}" target="_blank" rel="noopener noreferrer">${instructionId}</a>`;
}

function statusBadge(status) {
  const level = status || "DRAFT";
  return `<span class="badge badge-status badge-${level}">${level}</span>`;
}

function passesFilters(instruction) {
  const status = statusFilter.value;
  const lob = lobFilter.value;
  if (status !== "ALL" && instruction.status !== status) {
    return false;
  }
  if (lob !== "ALL" && instruction.owning_lob !== lob) {
    return false;
  }
  return true;
}

function updateFilterOptions() {
  const statuses = new Set(instructions.map((item) => item.status).filter(Boolean));
  const lobs = new Set(instructions.map((item) => item.owning_lob).filter(Boolean));

  const currentStatus = statusFilter.value;
  statusFilter.innerHTML = '<option value="ALL">All</option>';
  [...statuses].sort().forEach((status) => {
    const option = document.createElement("option");
    option.value = status;
    option.textContent = status;
    statusFilter.appendChild(option);
  });
  if ([...statuses, "ALL"].includes(currentStatus)) {
    statusFilter.value = currentStatus;
  }

  const currentLob = lobFilter.value;
  lobFilter.innerHTML = '<option value="ALL">All</option>';
  [...lobs].sort().forEach((lob) => {
    const option = document.createElement("option");
    option.value = lob;
    option.textContent = lob;
    lobFilter.appendChild(option);
  });
  if ([...lobs, "ALL"].includes(currentLob)) {
    lobFilter.value = currentLob;
  }
}

function renderTable() {
  tbody.innerHTML = "";
  const visible = instructions.filter(passesFilters);
  emptyState.classList.toggle("hidden", visible.length > 0);
  statTotal.textContent = String(visible.length);

  visible.forEach((instruction) => {
    const row = document.createElement("tr");
    row.className = `status-row status-${instruction.status || "DRAFT"}`;
    row.innerHTML = `
      <td class="col-instruction-id">${instructionIdLink(instruction.instruction_id)}</td>
      <td>${statusBadge(instruction.status)}</td>
      <td class="mono">${instruction.instruction_type || "—"}</td>
      <td class="mono">${instruction.owning_lob || "—"}</td>
      <td class="mono">${instruction.wire_scope || "—"}</td>
      <td>
        <div class="mono">${instruction.created_by?.user_id || "—"}</div>
        <div class="muted">${instruction.created_by?.title || ""}</div>
      </td>
      <td class="mono">${formatTime(instruction.created_at)}</td>
      <td class="mono">${formatTime(instruction.updated_at)}</td>
      <td class="mono">${instruction.version_number ?? "—"}</td>
    `;
    tbody.appendChild(row);
  });
}

function setLoadStatus(state, label) {
  loadStatus.className = `status-pill status-${state}`;
  loadStatus.textContent = label;
}

async function loadInstructions() {
  if (!AdminAuth.loadSession()) {
    setLoadStatus("error", "Sign in required");
    return;
  }
  setLoadStatus("connecting", "Loading");
  try {
    const response = await AdminAuth.adminFetch("/api/ui/instructions?limit=500");
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
    const payload = await response.json();
    instructions = (payload.instructions || []).slice(0, MAX_ROWS);
    updateFilterOptions();
    renderTable();
    setLoadStatus("live", `Loaded ${instructions.length}`);
  } catch (error) {
    setLoadStatus("error", "Load failed");
    console.error(error);
  }
}

function connectStream() {
  if (pollTimer) {
    clearInterval(pollTimer);
  }
  pollTimer = setInterval(() => {
    if (!paused) {
      void loadInstructions();
    }
  }, 2000);
}

statusFilter.addEventListener("change", () => renderTable());
lobFilter.addEventListener("change", () => renderTable());

function setStatusHelpOpen(open) {
  statusHelpPanel.classList.toggle("hidden", !open);
  statusHelpBtn.setAttribute("aria-expanded", open ? "true" : "false");
}

statusHelpBtn.addEventListener("click", (event) => {
  event.preventDefault();
  event.stopPropagation();
  setStatusHelpOpen(statusHelpPanel.classList.contains("hidden"));
});

document.addEventListener("click", (event) => {
  if (statusHelpPanel.classList.contains("hidden")) {
    return;
  }
  if (statusHelpPanel.contains(event.target) || statusHelpBtn.contains(event.target)) {
    return;
  }
  setStatusHelpOpen(false);
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    setStatusHelpOpen(false);
  }
});

refreshBtn.addEventListener("click", () => void loadInstructions());

pauseBtn.addEventListener("click", () => {
  paused = !paused;
  pauseBtn.textContent = paused ? "Resume live feed" : "Pause live feed";
});

clearBtn.addEventListener("click", () => {
  instructions = [];
  renderTable();
});

AdminAuth.bindAdminAuthPanel({
  statusEl: document.getElementById("auth-status"),
  userEl: document.getElementById("auth-user"),
  passwordEl: document.getElementById("auth-password"),
  loginBtn: document.getElementById("auth-login-btn"),
  logoutBtn: document.getElementById("auth-logout-btn"),
  onAuthenticated: () => {
    void loadInstructions();
    connectStream();
  },
});
