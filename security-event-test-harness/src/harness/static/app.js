const logOutput = document.getElementById("log-output");
const patStatus = document.getElementById("pat-status");
const statInstructions = document.getElementById("stat-instructions");
const statEvents = document.getElementById("stat-events");
const actionGrid = document.getElementById("action-grid");
const clearLogButton = document.getElementById("clear-log");

let busy = false;

function appendLog(text, { error = false } = {}) {
  const stamp = new Date().toLocaleTimeString();
  const prefix = error ? "[error]" : "[info]";
  logOutput.textContent += `${stamp} ${prefix} ${text}\n`;
  logOutput.scrollTop = logOutput.scrollHeight;
}

function setBusy(nextBusy) {
  busy = nextBusy;
  actionGrid.querySelectorAll("button").forEach((button) => {
    button.disabled = nextBusy;
  });
}

async function refreshStatus() {
  try {
    const response = await fetch("/api/status");
    if (!response.ok) {
      throw new Error(`status HTTP ${response.status}`);
    }
    const data = await response.json();

    if (data.zitadel_configured) {
      patStatus.textContent = "ZITADEL ready";
      patStatus.className = "status-pill status-live";
    } else {
      patStatus.textContent = "ZITADEL PAT missing";
      patStatus.className = "status-pill status-error";
    }

    const counts = data.instruction_counts || {};
    const parts = Object.entries(counts)
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([status, count]) => `${status}: ${count}`);
    statInstructions.textContent = parts.length
      ? `${data.instruction_total} (${parts.join(", ")})`
      : String(data.instruction_total ?? 0);

    statEvents.textContent =
      data.security_event_count >= 0 ? String(data.security_event_count) : "—";
  } catch (error) {
    patStatus.textContent = "Status unavailable";
    patStatus.className = "status-pill status-error";
    console.error(error);
  }
}

async function runAction(action, count) {
  if (busy) {
    return;
  }

  setBusy(true);
  const label = action.replace(/-/g, " ");
  appendLog(`Starting ${label}${count ? ` (count=${count})` : ""}...`);

  try {
    const response = await fetch(`/api/actions/${action}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(count ? { count } : {}),
    });

    const data = await response.json();
    if (!response.ok) {
      const detail = data.detail || JSON.stringify(data);
      throw new Error(detail);
    }

    for (const line of data.logs || []) {
      appendLog(line, { error: !data.ok });
    }

    appendLog(
      `Finished ${label}: succeeded=${data.succeeded}, failed=${data.failed}, skipped=${data.skipped}`,
      { error: !data.ok },
    );
  } catch (error) {
    appendLog(`${label} failed: ${error.message}`, { error: true });
  } finally {
    setBusy(false);
    await refreshStatus();
  }
}

actionGrid.addEventListener("click", (event) => {
  const button = event.target.closest("button");
  if (!button || button.disabled) {
    return;
  }

  const card = button.closest(".action-card");
  if (!card) {
    return;
  }

  const action = card.dataset.action;
  const input = card.querySelector('input[type="number"]');
  const count = input ? Number.parseInt(input.value, 10) : null;

  if (input && (!Number.isFinite(count) || count < 1)) {
    appendLog("Enter a valid count (at least 1).", { error: true });
    return;
  }

  void runAction(action, count);
});

clearLogButton.addEventListener("click", () => {
  logOutput.textContent = "";
});

void refreshStatus();
setInterval(() => {
  void refreshStatus();
}, 15000);
