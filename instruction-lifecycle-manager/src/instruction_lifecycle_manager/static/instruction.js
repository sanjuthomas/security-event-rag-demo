const subtitle = document.getElementById("instruction-id-subtitle");
const errorEl = document.getElementById("detail-error");
const summaryEl = document.getElementById("detail-summary");
const jsonSection = document.getElementById("detail-json-section");
const jsonEl = document.getElementById("detail-json");
const copyBtn = document.getElementById("copy-json-btn");

function instructionIdFromPath() {
  const parts = window.location.pathname.split("/").filter(Boolean);
  if (parts.length >= 3 && parts[0] === "ui" && parts[1] === "instructions") {
    return decodeURIComponent(parts.slice(2).join("/"));
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

function statusBadge(status) {
  const level = status || "DRAFT";
  return `<span class="badge badge-status badge-${level}">${level}</span>`;
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

function renderSummary(instruction) {
  summaryEl.innerHTML = `
    <div class="detail-card">
      <div class="detail-card-header">
        ${statusBadge(instruction.status)}
        <span class="detail-action mono">${instruction.instruction_type || "—"}</span>
        <span class="detail-action mono">${instruction.owning_lob || "—"}</span>
      </div>
      <dl class="detail-grid">
        ${field("Instruction ID", instruction.instruction_id)}
        ${field("Version", instruction.version_number)}
        ${field("Wire scope", instruction.wire_scope)}
        ${field("Currency", instruction.currency)}
        ${field("Charge bearer", instruction.charge_bearer)}
        ${field("Debtor", instruction.debtor?.name)}
        ${field("Creditor", instruction.creditor?.name)}
        ${field("Funding account", instruction.funding_account?.account_id)}
        ${field("Created by", instruction.created_by?.user_id)}
        ${field("Creator title", instruction.created_by?.title)}
        ${field("Approved by", instruction.approved_by?.user_id)}
        ${field("Effective date", formatTime(instruction.effective_date))}
        ${field("End date", formatTime(instruction.end_date))}
        ${field("Created (UTC)", formatTime(instruction.created_at))}
        ${field("Updated (UTC)", formatTime(instruction.updated_at))}
        ${field("Submitted (UTC)", formatTime(instruction.submitted_at))}
        ${field("Approved (UTC)", formatTime(instruction.approved_at))}
        ${field("Usage count", instruction.usage_count)}
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

async function loadInstruction() {
  const instructionId = instructionIdFromPath();
  if (!instructionId) {
    showError("Missing instruction id in URL.");
    subtitle.textContent = "Invalid URL";
    return;
  }

  subtitle.textContent = instructionId;
  document.title = `Instruction · ${instructionId}`;

  try {
    const response = await fetch(
      `/api/ui/instructions/${encodeURIComponent(instructionId)}`
    );
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      throw new Error(payload.detail || `HTTP ${response.status}`);
    }
    const payload = await response.json();
    const instruction = payload.instruction;
    renderSummary(instruction);
    summaryEl.classList.remove("hidden");
    jsonEl.textContent = JSON.stringify(instruction, null, 2);
    jsonSection.classList.remove("hidden");
  } catch (error) {
    showError(`Could not load instruction: ${error.message}`);
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

loadInstruction();
