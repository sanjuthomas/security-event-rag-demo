const componentsGrid = document.getElementById("components-grid");
const modeHint = document.getElementById("mode-hint");
const searchForm = document.getElementById("search-form");
const searchBtn = document.getElementById("search-btn");
const queryInput = document.getElementById("query-input");
const limitInput = document.getElementById("limit-input");
const neo4jActionWrap = document.getElementById("neo4j-action-wrap");
const neo4jActionInput = document.getElementById("neo4j-action-input");
const resultsTitle = document.getElementById("results-title");
const resultsMeta = document.getElementById("results-meta");
const resultsEmpty = document.getElementById("results-empty");
const resultsList = document.getElementById("results-list");
const resultsDetail = document.getElementById("results-detail");
const clearResultsBtn = document.getElementById("clear-results-btn");
const modeTabs = document.querySelectorAll(".mode-tab");

const MODE_HINTS = {
  hybrid: "Dense embeddings + BM25 lexical search fused with reciprocal rank fusion (RRF).",
  vector: "Semantic search using Ollama dense embeddings stored in Qdrant.",
  bm25: "Lexical keyword search using Qdrant BM25 sparse vectors.",
  neo4j: "Cypher text search over SecurityEvent nodes in the Neo4j graph.",
};

let mode = "hybrid";
let selectedCard = null;
let componentsTimer = null;

async function apiFetch(url, options = {}) {
  return AdminAuth.adminFetch(url, options);
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function setMode(nextMode) {
  mode = nextMode;
  modeTabs.forEach((tab) => {
    tab.classList.toggle("active", tab.dataset.mode === mode);
  });
  modeHint.textContent = MODE_HINTS[mode] || "";
  const neo4jMode = mode === "neo4j";
  neo4jActionWrap.classList.toggle("hidden", !neo4jMode);
  neo4jActionWrap.setAttribute("aria-hidden", neo4jMode ? "false" : "true");
  resultsTitle.textContent =
    mode === "neo4j" ? "Neo4j graph matches" : `Qdrant ${mode} matches`;
}

function clearResults() {
  resultsList.innerHTML = "";
  resultsList.classList.add("hidden");
  resultsDetail.classList.add("hidden");
  resultsDetail.textContent = "";
  resultsMeta.textContent = "";
  resultsEmpty.classList.remove("hidden", "error");
  resultsEmpty.textContent = "Run a search to query indexed security events.";
  clearResultsBtn.classList.add("hidden");
  selectedCard = null;
}

const COMPONENT_LABELS = {
  kafka: "Kafka",
  ollama: "Ollama",
  qdrant_vector: "Qdrant · Vector",
  qdrant_bm25: "Qdrant · BM25",
  neo4j: "Neo4j",
};

function componentDetail(key, component) {
  if (key === "kafka") {
    if (component.status === "disabled") {
      return "Consumer disabled";
    }
    return [
      component.topic,
      component.consumer === "running" ? "consumer running" : null,
      component.brokers != null ? `${component.brokers} broker(s)` : null,
    ]
      .filter(Boolean)
      .join(" · ");
  }
  if (key === "ollama") {
    return [
      component.model,
      component.embeddings === "ready" ? `dim ${component.dimension}` : "not warmed up",
      component.models_available != null ? `${component.models_available} model(s)` : null,
    ]
      .filter(Boolean)
      .join(" · ");
  }
  if (key === "qdrant_vector" || key === "qdrant_bm25") {
    const points =
      component.points_count != null ? `${component.points_count} point(s)` : null;
    return [component.collection, component.vector, points].filter(Boolean).join(" · ");
  }
  if (key === "neo4j") {
    const nodes =
      component.total_nodes != null ? `${component.total_nodes} node(s)` : null;
    return [component.uri, nodes].filter(Boolean).join(" · ");
  }
  return component.detail || "";
}

function renderComponents(components) {
  componentsGrid.innerHTML = "";
  for (const key of Object.keys(COMPONENT_LABELS)) {
    const component = components[key] || { ok: false, status: "down" };
    const card = document.createElement("article");
    card.className = `component-card status-${component.status || (component.ok ? "up" : "down")}`;
    card.innerHTML = `
      <div class="component-head">
        <span class="component-name">${escapeHtml(COMPONENT_LABELS[key])}</span>
        <span class="component-pill ${escapeHtml(component.status || "down")}">${escapeHtml(component.status || "down")}</span>
      </div>
      <p class="component-detail">${escapeHtml(componentDetail(key, component) || component.detail || "—")}</p>
    `;
    if (!component.ok && component.detail) {
      card.title = component.detail;
    }
    componentsGrid.appendChild(card);
  }
}

async function refreshComponents() {
  if (!AdminAuth.loadSession()) {
    componentsGrid.innerHTML = '<div class="component-card status-down">Admin sign-in required</div>';
    return;
  }
  try {
    const response = await apiFetch("/api/stats");
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
    const data = await response.json();
    renderComponents(data.components || {});
  } catch (error) {
    componentsGrid.innerHTML = `<div class="component-card status-down">Component status unavailable: ${escapeHtml(error.message)}</div>`;
  }
}

async function refreshStats() {
  await refreshComponents();
}

function securityEventSummary(result) {
  const event = result.security_event || result.payload?.security_event || result.event || result;
  const ctx = event.event || {};
  return {
    eventId: result.event_id || event.event_id,
    message: event.message || result.search_text || "—",
    action: ctx.action || event.action || "—",
    severity: event.severity || "—",
    outcome: ctx.outcome || event.outcome || "—",
    score: result.score,
  };
}

function showDetail(payload) {
  resultsDetail.classList.remove("hidden");
  resultsDetail.textContent = JSON.stringify(payload, null, 2);
}

function renderVectorResults(results) {
  resultsList.innerHTML = "";
  results.forEach((result) => {
    const summary = securityEventSummary(result);
    const card = document.createElement("article");
    card.className = "result-card";
    const badgeClass = summary.severity === "ALERT" ? "badge badge-ALERT" : "badge";
    card.innerHTML = `
      <div class="result-head">
        <span class="${badgeClass}">${escapeHtml(summary.severity)}</span>
        <span class="mono score">score ${summary.score?.toFixed?.(4) ?? "—"}</span>
      </div>
      <p class="result-message">${escapeHtml(summary.message)}</p>
      <div class="result-meta mono">
        <span>${escapeHtml(summary.action)}</span>
        <span>${escapeHtml(summary.outcome)}</span>
        <span>${escapeHtml(summary.eventId || "—")}</span>
      </div>
    `;
    card.addEventListener("click", () => {
      if (selectedCard) {
        selectedCard.classList.remove("selected");
      }
      card.classList.add("selected");
      selectedCard = card;
      showDetail(result);
    });
    resultsList.appendChild(card);
  });
}

function renderNeo4jResults(events) {
  resultsList.innerHTML = "";
  events.forEach((event) => {
    const card = document.createElement("article");
    card.className = "result-card";
    const badgeClass = event.severity === "ALERT" ? "badge badge-ALERT" : "badge";
    card.innerHTML = `
      <div class="result-head">
        <span class="${badgeClass}">${escapeHtml(event.severity || "—")}</span>
        <span class="mono">${escapeHtml(event.action || "—")}</span>
      </div>
      <p class="result-message">${escapeHtml(event.message || "—")}</p>
      <div class="result-meta mono">
        <span>${escapeHtml(event.outcome || "—")}</span>
        <span>${escapeHtml(event.event_id || "—")}</span>
      </div>
    `;
    card.addEventListener("click", async () => {
      if (!event.event_id) {
        showDetail(event);
        return;
      }
      if (selectedCard) {
        selectedCard.classList.remove("selected");
      }
      card.classList.add("selected");
      selectedCard = card;
      try {
        const response = await apiFetch(`/api/graph/events/${encodeURIComponent(event.event_id)}`);
        const payload = await response.json();
        if (!response.ok) {
          throw new Error(payload.detail || `HTTP ${response.status}`);
        }
        showDetail(payload);
      } catch (error) {
        showDetail({ error: error.message, event });
      }
    });
    resultsList.appendChild(card);
  });
}

modeTabs.forEach((tab) => {
  tab.addEventListener("click", () => setMode(tab.dataset.mode));
});

clearResultsBtn.addEventListener("click", clearResults);

searchForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const query = queryInput.value.trim();
  const limit = Number.parseInt(limitInput.value, 10) || 10;
  if (!query) {
    return;
  }

  searchBtn.disabled = true;
  resultsEmpty.classList.remove("error");
  resultsEmpty.textContent = "Searching…";
  resultsEmpty.classList.remove("hidden");
  resultsList.classList.add("hidden");
  resultsDetail.classList.add("hidden");
  resultsMeta.textContent = "";

  try {
    let response;
    if (mode === "neo4j") {
      const params = new URLSearchParams({ q: query, limit: String(limit) });
      const action = neo4jActionInput.value.trim();
      if (action) {
        params.set("action", action);
      }
      response = await apiFetch(`/api/graph/events?${params.toString()}`);
    } else {
      response = await apiFetch(`/api/search/${mode}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query, limit }),
      });
    }

    const payload = await response.json();
    if (!response.ok) {
      throw new Error(
        typeof payload.detail === "string"
          ? payload.detail
          : JSON.stringify(payload.detail || payload),
      );
    }

    const results = payload.results || payload.events || [];
    if (results.length === 0) {
      resultsEmpty.classList.remove("hidden");
      resultsEmpty.textContent =
        "No matches. Generate events via the test harness, then wait for Kafka ETL to index them.";
      resultsList.classList.add("hidden");
    } else {
      resultsEmpty.classList.add("hidden");
      resultsList.classList.remove("hidden");
      if (mode === "neo4j") {
        renderNeo4jResults(results);
        resultsMeta.textContent = `${payload.count || 0} graph match(es)`;
      } else {
        renderVectorResults(results);
        resultsMeta.textContent = `${payload.count || 0} hit(s) · ${payload.mode}`;
      }
      clearResultsBtn.classList.remove("hidden");
    }
  } catch (error) {
    resultsList.classList.add("hidden");
    resultsEmpty.classList.remove("hidden");
    resultsEmpty.classList.add("error");
    resultsEmpty.textContent = `Search failed: ${error.message}`;
  } finally {
    searchBtn.disabled = false;
  }
});

setMode("hybrid");

function startComponentsPolling() {
  if (componentsTimer) {
    clearInterval(componentsTimer);
  }
  void refreshComponents();
  void refreshChunkStats();
  componentsTimer = setInterval(() => {
    void refreshComponents();
    void refreshChunkStats();
  }, 20000);
}

// ── Indexed text size monitor ─────────────────────────────────────────────

const chunkStatsEmpty = document.getElementById("chunk-stats-empty");
const chunkStatsContent = document.getElementById("chunk-stats-content");
const chunkStatsSummary = document.getElementById("chunk-stats-summary");
const chunkStatsBody = document.getElementById("chunk-stats-body");
const chunkStatsRefreshBtn = document.getElementById("chunk-stats-refresh-btn");
const chunkEmbedLimit = document.getElementById("chunk-embed-limit");

function formatNumber(value) {
  return Number(value ?? 0).toLocaleString();
}

function renderChunkStats(data) {
  const summary = data.summary || {};
  const chars = summary.char_count || {};
  const words = summary.word_count || {};
  const tokens = summary.estimated_tokens || {};
  const maxTokens = tokens.max || 0;
  const contextLimit = data.embedding_context_tokens || 32768;
  const headroomPct = contextLimit
    ? Math.round((maxTokens / contextLimit) * 1000) / 10
    : 0;

  chunkStatsSummary.innerHTML = `
    <article class="chunk-stat-card">
      <span class="chunk-stat-label">Points indexed</span>
      <strong class="chunk-stat-value">${formatNumber(data.points_count)}</strong>
      <span class="chunk-stat-detail mono">${escapeHtml(data.collection || "—")}</span>
    </article>
    <article class="chunk-stat-card">
      <span class="chunk-stat-label">Largest text</span>
      <strong class="chunk-stat-value">${formatNumber(chars.max)} chars</strong>
      <span class="chunk-stat-detail">~${formatNumber(tokens.max)} tokens · ${headroomPct}% of ${formatNumber(contextLimit)} limit</span>
    </article>
    <article class="chunk-stat-card">
      <span class="chunk-stat-label">Average text</span>
      <strong class="chunk-stat-value">${formatNumber(chars.avg)} chars</strong>
      <span class="chunk-stat-detail">${formatNumber(words.avg)} words avg · median ${formatNumber(chars.median)} chars</span>
    </article>
    <article class="chunk-stat-card">
      <span class="chunk-stat-label">Indexing model</span>
      <strong class="chunk-stat-value">No chunking</strong>
      <span class="chunk-stat-detail">1 point = 1 record · embedded field = search_text subset</span>
    </article>
  `;

  if (chunkEmbedLimit && contextLimit) {
    chunkEmbedLimit.textContent = `${Math.round(contextLimit / 1000)}K`;
  }

  chunkStatsBody.innerHTML = "";
  (data.top_chunks || []).forEach((row) => {
    const tr = document.createElement("tr");
    const recordId =
      row.event_id || row.payment_id || row.instruction_id || row.record_id || row.point_id;
    tr.innerHTML = `
      <td class="mono">${row.rank ?? "—"}</td>
      <td><span class="chunk-source-pill">${escapeHtml(row.source || "unknown")}</span></td>
      <td class="mono chunk-record-id">${escapeHtml(recordId || "—")}</td>
      <td class="mono">${formatNumber(row.char_count)}</td>
      <td class="mono">${formatNumber(row.word_count)}</td>
      <td class="mono">${formatNumber(row.estimated_tokens)}</td>
      <td class="chunk-preview" title="${escapeHtml(row.preview || "")}">${escapeHtml(row.preview || "—")}</td>
    `;
    chunkStatsBody.appendChild(tr);
  });

  chunkStatsEmpty.classList.add("hidden");
  chunkStatsContent.classList.remove("hidden");
}

async function refreshChunkStats() {
  if (!AdminAuth.loadSession()) {
    chunkStatsContent.classList.add("hidden");
    chunkStatsEmpty.classList.remove("hidden");
    chunkStatsEmpty.textContent = "Sign in to load chunk size stats from Qdrant.";
    return;
  }

  chunkStatsRefreshBtn.disabled = true;
  try {
    const response = await apiFetch("/api/vector/chunk-stats?limit=10");
    const data = await response.json();
    if (!response.ok) {
      throw new Error(typeof data.detail === "string" ? data.detail : `HTTP ${response.status}`);
    }
    if (!data.points_count) {
      chunkStatsContent.classList.add("hidden");
      chunkStatsEmpty.classList.remove("hidden");
      chunkStatsEmpty.textContent = "No indexed points yet — generate events via the test harness.";
      return;
    }
    renderChunkStats(data);
  } catch (error) {
    chunkStatsContent.classList.add("hidden");
    chunkStatsEmpty.classList.remove("hidden");
    chunkStatsEmpty.textContent = `Chunk stats unavailable: ${error.message}`;
  } finally {
    chunkStatsRefreshBtn.disabled = false;
  }
}

if (chunkStatsRefreshBtn) {
  chunkStatsRefreshBtn.addEventListener("click", () => {
    void refreshChunkStats();
  });
}

AdminAuth.bindAdminAuthPanel({
  statusEl: document.getElementById("auth-status"),
  userEl: document.getElementById("auth-user"),
  passwordEl: document.getElementById("auth-password"),
  loginBtn: document.getElementById("auth-login-btn"),
  logoutBtn: document.getElementById("auth-logout-btn"),
  onAuthenticated: () => {
    startComponentsPolling();
  },
});

// ── Text → Cypher pane ────────────────────────────────────────────────────

const cypherForm = document.getElementById("cypher-form");
const cypherModeSelect = document.getElementById("cypher-mode-select");
const cypherQuestionInput = document.getElementById("cypher-question-input");
const cypherGenerateBtn = document.getElementById("cypher-generate-btn");
const cypherGenerateStatus = document.getElementById("cypher-generate-status");
const cypherOutputWrap = document.getElementById("cypher-output-wrap");
const cypherOutput = document.getElementById("cypher-output");
const cypherValidBadge = document.getElementById("cypher-valid-badge");
const cypherErrorMsg = document.getElementById("cypher-error-msg");
const cypherCopyBtn = document.getElementById("cypher-copy-btn");
const cypherRunBtn = document.getElementById("cypher-run-btn");
const cypherRunStatus = document.getElementById("cypher-run-status");
const cypherResultsWrap = document.getElementById("cypher-results-wrap");
const cypherResultsTitle = document.getElementById("cypher-results-title");
const cypherResultsOutput = document.getElementById("cypher-results-output");
const cypherClearBtn = document.getElementById("cypher-clear-btn");

let cypherBusy = false;

function setCypherBusy(next) {
  cypherBusy = next;
  cypherGenerateBtn.disabled = next;
  cypherRunBtn.disabled = next;
}

function showCypherResult(data) {
  cypherResultsTitle.textContent = `Neo4j result — ${data.row_count} row(s)`;
  cypherResultsOutput.textContent = JSON.stringify(data.rows, null, 2);
  cypherResultsWrap.classList.remove("hidden");
}

function clearCypherResults() {
  cypherResultsWrap.classList.add("hidden");
  cypherResultsOutput.textContent = "";
  cypherRunStatus.textContent = "";
}

cypherForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (cypherBusy) return;

  const question = cypherQuestionInput.value.trim();
  if (!question) return;

  setCypherBusy(true);
  cypherGenerateStatus.textContent = "Generating… (may take 20–60 s)";
  cypherOutputWrap.classList.add("hidden");
  clearCypherResults();

  try {
    const response = await apiFetch("/api/cypher/generate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question, mode: cypherModeSelect.value }),
    });
    const data = await response.json();
    if (!response.ok) {
      const msg = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail || data);
      throw new Error(msg);
    }

    cypherOutput.value = data.cypher || "";
    cypherOutputWrap.classList.remove("hidden");

    if (data.valid) {
      cypherValidBadge.textContent = "valid";
      cypherValidBadge.style.background = "var(--color-ok, #3a7d44)";
      cypherErrorMsg.classList.add("hidden");
    } else {
      cypherValidBadge.textContent = "invalid";
      cypherValidBadge.style.background = "var(--color-warn, #a0522d)";
      cypherErrorMsg.textContent = data.error || "Validation failed";
      cypherErrorMsg.classList.remove("hidden");
    }

    cypherGenerateStatus.textContent = `Generated via ${data.model || "llama3:8b"}`;
  } catch (error) {
    cypherGenerateStatus.textContent = `Error: ${error.message}`;
  } finally {
    setCypherBusy(false);
  }
});

cypherCopyBtn.addEventListener("click", () => {
  const text = cypherOutput.value;
  if (!text) return;
  navigator.clipboard.writeText(text).then(() => {
    const prev = cypherCopyBtn.textContent;
    cypherCopyBtn.textContent = "Copied!";
    setTimeout(() => { cypherCopyBtn.textContent = prev; }, 1500);
  });
});

cypherRunBtn.addEventListener("click", async () => {
  if (cypherBusy) return;
  const cypher = cypherOutput.value.trim();
  if (!cypher) return;

  setCypherBusy(true);
  cypherRunStatus.textContent = "Running…";
  clearCypherResults();

  try {
    const response = await apiFetch("/api/cypher/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ cypher }),
    });
    const data = await response.json();
    if (!response.ok) {
      const msg = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail || data);
      throw new Error(msg);
    }
    showCypherResult(data);
    cypherRunStatus.textContent = `${data.row_count} row(s) returned`;
  } catch (error) {
    cypherRunStatus.textContent = `Run failed: ${error.message}`;
  } finally {
    setCypherBusy(false);
  }
});

cypherClearBtn.addEventListener("click", () => {
  clearCypherResults();
  cypherOutputWrap.classList.add("hidden");
  cypherOutput.value = "";
  cypherGenerateStatus.textContent = "";
  cypherValidBadge.textContent = "";
  cypherErrorMsg.classList.add("hidden");
});
