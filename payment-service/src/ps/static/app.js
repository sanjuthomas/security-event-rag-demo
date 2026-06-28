const MAX_ROWS = 500;

const tbody = document.getElementById("payments-body");
const emptyState = document.getElementById("empty-state");
const loadStatus = document.getElementById("load-status");
const statTotal = document.getElementById("stat-total");
const statusFilter = document.getElementById("status-filter");
const instructionFilter = document.getElementById("instruction-filter");
const lobFilter = document.getElementById("lob-filter");
const typeFilter = document.getElementById("type-filter");
const refreshBtn = document.getElementById("refresh-btn");
const pauseBtn = document.getElementById("pause-btn");
const clearBtn = document.getElementById("clear-btn");

let payments = [];
let paused = false;
let pollTimer = null;

function formatTime(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toISOString().replace("T", " ").replace(".000Z", "Z");
}

function paymentIdLink(paymentId) {
  if (!paymentId) return "—";
  const href = `/ui/payments/${encodeURIComponent(paymentId)}`;
  return `<a class="id-link mono" href="${href}">${paymentId}</a>`;
}

function instructionLink(instructionId) {
  if (!instructionId) return "—";
  const href = `http://localhost:8000/ui/instructions/${encodeURIComponent(instructionId)}`;
  return `<a class="id-link mono" href="${href}" target="_blank" rel="noopener">${instructionId}</a>`;
}

function statusBadge(status) {
  return `<span class="badge badge-${status || "PENDING"}">${status || "PENDING"}</span>`;
}

function formatAmount(amount, currency) {
  if (amount === null || amount === undefined) return "—";
  const formatted = Number(amount).toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
  return `<span class="amount-cell">${formatted}</span>`;
}

function passesFilters(p) {
  if (statusFilter.value !== "ALL" && p.status !== statusFilter.value) return false;
  if (lobFilter.value !== "ALL" && p.owning_lob !== lobFilter.value) return false;
  if (typeFilter.value !== "ALL" && p.instruction_type !== typeFilter.value) return false;
  return true;
}

function updateLobOptions() {
  const lobs = new Set(payments.map((p) => p.owning_lob).filter(Boolean));
  const current = lobFilter.value;
  lobFilter.innerHTML = '<option value="ALL">All</option>';
  [...lobs].sort().forEach((lob) => {
    const opt = document.createElement("option");
    opt.value = lob;
    opt.textContent = lob;
    lobFilter.appendChild(opt);
  });
  if ([...lobs, "ALL"].includes(current)) lobFilter.value = current;
}

function renderTable({ highlightFirst = false } = {}) {
  tbody.innerHTML = "";
  const visible = payments.filter(passesFilters);
  emptyState.classList.toggle("hidden", visible.length > 0);
  statTotal.textContent = String(visible.length);

  visible.forEach((p, index) => {
    const row = document.createElement("tr");
    if (highlightFirst && index === 0) row.classList.add("row-new");

    const creatorId = p.created_by?.user_id || "—";
    const creatorTitle = p.created_by?.title || "";
    const approverId = p.approved_by?.user_id || "—";

    row.innerHTML = `
      <td class="col-id">${paymentIdLink(p.payment_id)}</td>
      <td class="col-id">${instructionLink(p.instruction_id)}</td>
      <td class="mono">${p.instruction_version ?? "—"}</td>
      <td>${statusBadge(p.status)}</td>
      <td class="mono">${p.instruction_type || "—"}</td>
      <td class="mono">${p.owning_lob || "—"}</td>
      <td class="mono">${formatAmount(p.amount)}</td>
      <td class="mono">${p.currency || "—"}</td>
      <td class="mono">${p.value_date || "—"}</td>
      <td>
        <div class="mono">${creatorId}</div>
        <div class="muted">${creatorTitle}</div>
      </td>
      <td class="mono">${approverId}</td>
      <td class="mono">${formatTime(p.created_at)}</td>
      <td class="mono">${formatTime(p.updated_at)}</td>
    `;
    tbody.appendChild(row);
  });

  if (highlightFirst) {
    const firstRow = tbody.querySelector("tr.row-new");
    if (firstRow) window.setTimeout(() => firstRow.classList.remove("row-new"), 1200);
  }
}

function buildPaymentsUrl() {
  const params = new URLSearchParams({ limit: String(MAX_ROWS) });
  const instructionId = instructionFilter.value.trim();
  if (instructionId) {
    params.set("instruction_id", instructionId);
  }
  return `/api/ui/payments?${params.toString()}`;
}

function setLoadStatus(state, label) {
  loadStatus.className = `status-pill status-${state}`;
  loadStatus.textContent = label;
}

async function loadPayments() {
  if (!AdminAuth.loadSession()) {
    setLoadStatus("error", "Sign in required");
    return;
  }
  setLoadStatus("connecting", "Loading");
  try {
    const response = await AdminAuth.adminFetch(buildPaymentsUrl());
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const payload = await response.json();
    payments = payload.payments || [];
    updateLobOptions();
    renderTable();
    setLoadStatus("live", `Loaded ${payments.length}`);
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
      void loadPayments();
    }
  }, 2000);
}

statusFilter.addEventListener("change", () => renderTable());
instructionFilter.addEventListener("change", () => void loadPayments());
instructionFilter.addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    event.preventDefault();
    void loadPayments();
  }
});
lobFilter.addEventListener("change", () => renderTable());
typeFilter.addEventListener("change", () => renderTable());

refreshBtn.addEventListener("click", () => void loadPayments());

pauseBtn.addEventListener("click", () => {
  paused = !paused;
  pauseBtn.textContent = paused ? "Resume live feed" : "Pause live feed";
});

clearBtn.addEventListener("click", () => {
  payments = [];
  renderTable();
});

AdminAuth.bindAdminAuthPanel({
  statusEl: document.getElementById("auth-status"),
  userEl: document.getElementById("auth-user"),
  passwordEl: document.getElementById("auth-password"),
  loginBtn: document.getElementById("auth-login-btn"),
  logoutBtn: document.getElementById("auth-logout-btn"),
  onAuthenticated: () => {
    void loadPayments();
    connectStream();
  },
});
