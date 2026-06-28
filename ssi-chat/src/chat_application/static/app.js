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
const authStatus = document.getElementById("auth-status");
const authUser = document.getElementById("auth-user");
const authPassword = document.getElementById("auth-password");
const authLoginBtn = document.getElementById("auth-login-btn");
const authLogoutBtn = document.getElementById("auth-logout-btn");

const AUTH_STORAGE_KEY = "ssi-chat-session";
const ASSISTANT_NAME = "PolicyPilot";

/** @type {{ role: 'user' | 'assistant', content: string }[]} */
let history = [];

/** @type {{ user_id: string, session_id: string, session_token: string } | null} */
let session = null;

function loadSession() {
  try {
    const raw = localStorage.getItem(AUTH_STORAGE_KEY);
    session = raw ? JSON.parse(raw) : null;
  } catch {
    session = null;
  }
  updateAuthUi();
}

function saveSession() {
  if (session) {
    localStorage.setItem(AUTH_STORAGE_KEY, JSON.stringify(session));
  } else {
    localStorage.removeItem(AUTH_STORAGE_KEY);
  }
  updateAuthUi();
}

function updateAuthUi() {
  if (session) {
    authStatus.textContent = `Signed in as ${session.user_id}`;
    authStatus.classList.remove("muted");
    authUser.classList.add("hidden");
    authPassword.classList.add("hidden");
    authLoginBtn.classList.add("hidden");
    authLogoutBtn.classList.remove("hidden");
  } else {
    authStatus.textContent = "Not signed in";
    authStatus.classList.add("muted");
    authUser.classList.remove("hidden");
    authPassword.classList.remove("hidden");
    authLoginBtn.classList.remove("hidden");
    authLogoutBtn.classList.add("hidden");
  }
}

async function loadComplianceUsers() {
  try {
    const response = await fetch("/api/compliance-users");
    if (!response.ok) {
      return;
    }
    const data = await response.json();
    for (const user of data.users || []) {
      const option = document.createElement("option");
      option.value = user.user_id;
      option.textContent = `${user.display_name} (${user.user_id})`;
      authUser.appendChild(option);
    }
  } catch (error) {
    console.warn("could not load compliance users", error);
  }
}

async function login() {
  const userId = authUser.value;
  const password = authPassword.value;
  if (!userId || !password) {
    authStatus.textContent = "Select user and enter password";
    return;
  }

  authLoginBtn.disabled = true;
  try {
    const response = await fetch("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ user_id: userId, password }),
    });
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.detail || `HTTP ${response.status}`);
    }
    session = {
      user_id: payload.user_id,
      session_id: payload.session_id,
      session_token: payload.session_token,
    };
    saveSession();
    authPassword.value = "";
  } catch (error) {
    authStatus.textContent = `Login failed: ${error.message}`;
    authStatus.classList.add("muted");
  } finally {
    authLoginBtn.disabled = false;
  }
}

function logout() {
  session = null;
  saveSession();
}

function authHeaders() {
  if (!session) {
    return {};
  }
  return {
    Authorization: `Bearer ${session.session_token}`,
    "X-Session-Id": session.session_id,
  };
}

function appendMessage(role, content) {
  const wrap = document.createElement("div");
  wrap.className = `message ${role}`;
  wrap.innerHTML = `
    <div class="message-role">${role === "user" ? "You" : ASSISTANT_NAME}</div>
    <div class="message-body"></div>
  `;
  const body = wrap.querySelector(".message-body");
  if (role === "assistant") {
    body.innerHTML = renderAssistantMarkdown(content);
  } else {
    body.textContent = content;
  }
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

function getSelectedMode() {
  const checked = document.querySelector('input[name="mode"]:checked');
  return checked ? checked.value : "events";
}

async function sendMessage(text) {
  if (!session) {
    authStatus.textContent = "Sign in required before chatting";
    authStatus.classList.add("muted");
    return;
  }

  const mode = getSelectedMode();
  sendBtn.disabled = true;
  sendBtn.textContent = "Thinking…";

  const modeLabel = {
    events: "🔍 Events",
    instructions: "📋 Instructions",
    payments: "💳 Payments",
    all: "🔀 All entities",
  }[mode] || mode;
  appendMessage("user", `[${modeLabel}] ${text}`);
  history.push({ role: "user", content: text });

  try {
    const response = await fetch("/api/chat", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...authHeaders(),
      },
      body: JSON.stringify({ message: text, history: history.slice(0, -1), mode }),
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
    appendMessage("assistant", `${ASSISTANT_NAME} hit an error: ${error.message}`);
  } finally {
    sendBtn.disabled = false;
    sendBtn.textContent = "Send";
    input.focus();
  }
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  const text = input.value.trim();
  if (!text || sendBtn.disabled || !session) {
    if (!session) {
      authStatus.textContent = "Sign in required before chatting";
      authStatus.classList.add("muted");
    }
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
    "Chat cleared. Ask PolicyPilot a new question about security events, instructions, or payments."
  );
  metaEmpty.classList.remove("hidden");
  metaContent.classList.add("hidden");
  input.focus();
});

authLoginBtn.addEventListener("click", login);
authLogoutBtn.addEventListener("click", logout);
authPassword.addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    event.preventDefault();
    login();
  }
});

loadSession();
loadComplianceUsers();
input.focus();
