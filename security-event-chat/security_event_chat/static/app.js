const thread = document.getElementById("chat-thread");
const form = document.getElementById("chat-form");
const input = document.getElementById("chat-input");
const sendBtn = document.getElementById("send-btn");
const clearBtn = document.getElementById("clear-btn");
const metaEmpty = document.getElementById("meta-empty");
const metaContent = document.getElementById("meta-content");
const metaTiming = document.getElementById("meta-timing");
const metaCypher = document.getElementById("meta-cypher");
const metaSources = document.getElementById("meta-sources");

/** @type {{ role: 'user' | 'assistant', content: string }[]} */
let history = [];

function appendMessage(role, content) {
  const wrap = document.createElement("div");
  wrap.className = `message ${role}`;
  wrap.innerHTML = `
    <div class="message-role">${role === "user" ? "You" : "Assistant"}</div>
    <div class="message-body"></div>
  `;
  wrap.querySelector(".message-body").textContent = content;
  thread.appendChild(wrap);
  thread.scrollTop = thread.scrollHeight;
}

function shortId(value) {
  if (!value) return "—";
  const parts = String(value).split("-");
  return parts.length > 1 ? parts[parts.length - 1] : value;
}

function renderMeta(data) {
  metaEmpty.classList.add("hidden");
  metaContent.classList.remove("hidden");

  metaTiming.textContent = `Retrieval ${data.retrieval_ms ?? "—"} ms · Generation ${data.generation_ms ?? "—"} ms`;
  metaCypher.textContent = data.cypher || "(no Cypher generated)";

  metaSources.innerHTML = "";
  if (!data.sources || data.sources.length === 0) {
    metaSources.innerHTML = '<p class="muted">No event sources merged.</p>';
    return;
  }

  data.sources.forEach((source, index) => {
    const card = document.createElement("article");
    card.className = "source-card";
    card.innerHTML = `
      <div class="source-header">
        <span class="source-index">#${index + 1}</span>
        <span class="source-score mono">${source.score.toFixed(4)}</span>
      </div>
      <p class="source-tags mono">${(source.sources || []).join(" · ")}</p>
      <p class="source-ids mono">event ${shortId(source.event_id)} · instr ${shortId(source.instruction_id)}</p>
      <p class="source-summary">${source.summary || "—"}</p>
    `;
    metaSources.appendChild(card);
  });
}

async function sendMessage(text) {
  sendBtn.disabled = true;
  sendBtn.textContent = "Thinking…";
  appendMessage("user", text);
  history.push({ role: "user", content: text });

  try {
    const response = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: text, history: history.slice(0, -1) }),
    });
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.detail || `HTTP ${response.status}`);
    }
    appendMessage("assistant", payload.answer);
    history.push({ role: "assistant", content: payload.answer });
    if (history.length > 40) {
      history = history.slice(-40);
    }
    renderMeta(payload);
  } catch (error) {
    appendMessage("assistant", `Sorry, something went wrong: ${error.message}`);
  } finally {
    sendBtn.disabled = false;
    sendBtn.textContent = "Send";
    input.focus();
  }
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  const text = input.value.trim();
  if (!text || sendBtn.disabled) {
    return;
  }
  input.value = "";
  sendMessage(text);
});

input.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    form.requestSubmit();
  }
});

clearBtn.addEventListener("click", () => {
  history = [];
  thread.innerHTML = "";
  appendMessage(
    "assistant",
    "Chat cleared. Ask a new question about security events or instructions."
  );
  metaEmpty.classList.remove("hidden");
  metaContent.classList.add("hidden");
  input.focus();
});

input.focus();
