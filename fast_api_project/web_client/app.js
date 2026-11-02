/* =========================================================================
   NavDhan Console — shared client logic
   Loaded (after config.js) on every page. Handles: session storage,
   authenticated fetch, role-based route guarding, and small UI helpers.
   ========================================================================= */

const Session = {
  KEY: "navdhan_session",

  get() {
    try {
      return JSON.parse(sessionStorage.getItem(this.KEY)) || null;
    } catch {
      return null;
    }
  },

  set(data) {
    sessionStorage.setItem(this.KEY, JSON.stringify(data));
  },

  clear() {
    sessionStorage.removeItem(this.KEY);
  },

  isLoggedIn() {
    const s = this.get();
    return !!(s && s.access_token);
  },

  role() {
    const s = this.get();
    return s ? s.role : null;
  },

  username() {
    const s = this.get();
    return s ? s.username : null;
  },

  mustChangePassword() {
    const s = this.get();
    return !!(s && s.force_password_change);
  },
};

/**
 * Calls the API. Automatically attaches the bearer token (if logged in)
 * and throws a normal Error with the server's message on failure, so
 * callers can just try/catch and show err.message.
 */
async function apiFetch(path, { method = "GET", body, auth = true, form = false } = {}) {
  const headers = {};
  if (!form && body !== undefined) headers["Content-Type"] = "application/json";
  if (auth) {
    const session = Session.get();
    if (session && session.access_token) {
      headers["Authorization"] = `Bearer ${session.access_token}`;
    }
  }

  const response = await fetch(`${API_BASE_URL}${path}`, {
    method,
    headers,
    body: body === undefined ? undefined : (form ? body : JSON.stringify(body)),
  });

  let payload = null;
  try {
    payload = await response.json();
  } catch {
    // no JSON body (e.g. empty response) - fine for some 2xx responses
  }

  if (!response.ok) {
    const message =
      (payload && payload.error && payload.error.message) ||
      (payload && payload.detail) ||
      `Request failed (${response.status})`;
    const err = new Error(message);
    err.status = response.status;
    if (response.status === 401 && auth) {
      // Token missing/expired - bounce to login rather than showing a
      // confusing error on whatever page this was.
      Session.clear();
      window.location.href = "login.html";
    }
    throw err;
  }

  return payload;
}

/** Redirects to login.html if there's no session. Call at the top of
 * any page that requires being logged in. */
function requireAuth() {
  if (!Session.isLoggedIn()) {
    window.location.href = "login.html";
    return false;
  }
  if (Session.mustChangePassword() && !location.pathname.endsWith("change-password.html")) {
    window.location.href = "change-password.html";
    return false;
  }
  return true;
}

/** Redirects home if the logged-in user's role isn't in allowedRoles.
 * Call after requireAuth() on admin-only pages/sections. */
function requireRole(allowedRoles) {
  if (!allowedRoles.includes(Session.role())) {
    window.location.href = "dashboard.html";
    return false;
  }
  return true;
}

function showAlert(el, message, type = "error") {
  el.textContent = message;
  el.className = `alert show alert-${type}`;
}

function hideAlert(el) {
  el.className = "alert";
  el.textContent = "";
}

function stampClass(role) {
  return { admin: "stamp-admin", manager: "stamp-manager", user: "stamp-user" }[role] || "stamp-user";
}

function kycBadgeClass(status) {
  return { Verified: "badge-verified", Pending: "badge-pending", Rejected: "badge-rejected" }[status] || "badge-pending";
}

function formatDate(value) {
  if (!value) return "—";
  try {
    return new Date(value).toLocaleDateString("en-IN", { year: "numeric", month: "short", day: "numeric" });
  } catch {
    return value;
  }
}

/** Renders the sidebar, showing admin links only to admins, and
 * highlighting the current page. Call once per authenticated page. */
function renderSidebar(activePage) {
  const mount = document.getElementById("sidebar");
  if (!mount) return;
  const role = Session.role();
  const isAdmin = role === "admin";
  const isManager = role === "manager";

  const link = (href, label) =>
    `<a href="${href}" class="${activePage === href ? "active" : ""}">${label}</a>`;

  mount.innerHTML = `
    <div class="sidebar-brand">NavDhan Console</div>
    <nav>
      ${link("dashboard.html", "Investors")}
      ${link("transactions.html", "Transactions")}
      ${link("account-book.html", "Account Book")}
      ${link("account-book-3d.html", "Account Book 3D")}
      ${link("account-book-tree.html", "Account Book Tree")}
      ${link("curiosity-graph-deep.html", "Curiosity Graph")}
      ${link("curiosity-graph.html", "Knowledge Graph")}
      <div class="section-label">External Intelligence</div>
      ${link("ai.html", "NavDhan AI")}
      ${(isAdmin || isManager) ? link("market.html", "Market Research") : ""}
      ${(isAdmin || isManager) ? link(`${API_BASE_URL}/stock-intelligence`, "Stock Intelligence") : ""}
      ${isAdmin ? `<div class="section-label">Admin</div>` : ""}
      ${isAdmin ? link("admin-etl.html", "ETL Pipelines") : ""}
      ${isAdmin ? link("admin-requests.html", "Registration requests") : ""}
      ${isAdmin ? link("admin-users.html", "Users") : ""}
    </nav>
    <div class="sidebar-footer">
      <div class="stamp ${stampClass(role)}" style="margin-bottom:10px;">${role || ""}</div>
      <div style="font-size:13px;color:#CBD4C7;margin-bottom:10px;">${Session.username() || ""}</div>
      <a href="#" id="sidebarLogout" style="color:#CBD4C7;font-size:13px;text-decoration:underline;">Log out</a>
    </div>
  `;

  document.getElementById("sidebarLogout").addEventListener("click", (e) => {
    e.preventDefault();
    Session.clear();
    window.location.href = "login.html";
  });
}


/* =========================================================================
   Floating "Ask AI" widget - available on every page that calls
   renderSidebar() (i.e. every logged-in page except ai.html itself).
   Uses POST /ai/chat; the conversation survives page navigation via
   sessionStorage.
   ========================================================================= */
function mountAskAI() {
  if (document.getElementById("askAiFab") || location.pathname.endsWith("ai.html")) return;

  const KEY = "navdhan_askai_history";
  const load = () => { try { return JSON.parse(sessionStorage.getItem(KEY)) || []; } catch { return []; } };
  const save = () => { try { sessionStorage.setItem(KEY, JSON.stringify(history.slice(-20))); } catch {} };
  let history = load();

  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const cell = (v) => v === null || v === undefined ? "—"
    : typeof v === "number" ? v.toLocaleString("en-IN", { maximumFractionDigits: 2 }) : esc(v);

  const wrap = document.createElement("div");
  wrap.innerHTML = `
    <button id="askAiFab" class="askai-fab" type="button" aria-label="Ask AI">✦ Ask AI</button>
    <div id="askAiPanel" class="askai-panel" style="display:none;" role="dialog" aria-label="Ask AI">
      <div class="askai-head">
        <strong>Ask NavDhan AI</strong>
        <span>
          <a href="ai.html" title="Open full page">Open full page</a>
          <button type="button" id="askAiClear" title="New conversation">New</button>
          <button type="button" id="askAiClose" aria-label="Close">×</button>
        </span>
      </div>
      <div id="askAiLog" class="askai-log"></div>
      <form id="askAiForm" class="askai-form">
        <input id="askAiInput" maxlength="500" placeholder="Ask about investors or transactions…" autocomplete="off" required>
        <button type="submit" class="btn btn-gold btn-sm" id="askAiSend">Send</button>
      </form>
    </div>`;
  document.body.appendChild(wrap);

  const fab = document.getElementById("askAiFab");
  const panel = document.getElementById("askAiPanel");
  const log = document.getElementById("askAiLog");
  const input = document.getElementById("askAiInput");
  const sendBtn = document.getElementById("askAiSend");

  function bubble(role, html) {
    const d = document.createElement("div");
    d.className = `askai-bubble askai-${role}`;
    d.innerHTML = html;
    log.appendChild(d);
    log.scrollTop = log.scrollHeight;
    return d;
  }

  function renderResult(res) {
    let html = esc(res.answer);
    if (res.mode === "sql" && res.columns.length) {
      const rows = res.rows.slice(0, 8);
      html += `<div class="table-wrap"><table><thead><tr>${res.columns.map((c) => `<th>${esc(c)}</th>`).join("")}</tr></thead><tbody>` +
        rows.map((r) => `<tr>${r.map((v) => `<td>${cell(v)}</td>`).join("")}</tr>`).join("") +
        `</tbody></table></div><div class="askai-meta">${res.row_count} row${res.row_count === 1 ? "" : "s"}${res.rows.length > rows.length ? " · first 8 shown" : ""}</div>`;
    }
    return html;
  }

  function redraw() {
    log.innerHTML = "";
    bubble("assistant", "Hi! Ask me anything about your investors or transactions.");
    // Stored history keeps only text (+sql), so replay it as plain bubbles.
    history.forEach((m) => bubble(m.role, esc(m.content)));
  }

  fab.addEventListener("click", () => {
    const open = panel.style.display === "none";
    panel.style.display = open ? "flex" : "none";
    fab.style.display = open ? "none" : "";
    if (open) { redraw(); input.focus(); }
  });
  document.getElementById("askAiClose").addEventListener("click", () => { panel.style.display = "none"; fab.style.display = ""; });
  document.getElementById("askAiClear").addEventListener("click", () => { history = []; save(); redraw(); });

  document.getElementById("askAiForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const text = input.value.trim();
    if (!text) return;
    input.value = "";
    sendBtn.disabled = true;
    bubble("user", esc(text));
    history.push({ role: "user", content: text });
    const pending = bubble("assistant", `<em>Thinking…</em>`);
    try {
      const res = await apiFetch("/ai/chat", { method: "POST", body: { messages: history } });
      pending.innerHTML = renderResult(res);
      history.push({ role: "assistant", content: res.answer, sql: res.sql || undefined });
      save();
    } catch (err) {
      pending.className = "askai-bubble askai-error";
      pending.textContent = err.message;
      history.pop(); // don't keep the failed question
    } finally {
      sendBtn.disabled = false;
      input.focus();
    }
  });
}

// renderSidebar() runs once on every logged-in page - hook the widget onto it.
(function () {
  const original = renderSidebar;
  renderSidebar = function (activePage) {
    original(activePage);
    if (Session.isLoggedIn()) mountAskAI();
  };
})();
