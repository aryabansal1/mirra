const view = document.getElementById("view");
const toastEl = document.getElementById("toast");

const api = async (path, opts) => {
  const r = await fetch(path, opts);
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
  return r.json();
};

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmtDate = (iso) => (iso ? new Date(iso).toLocaleString([], { dateStyle: "medium", timeStyle: "short" }) : "—");
const statusText = { uploaded: "Queued", analyzing: "Analyzing", results_ready: "Results ready", failed: "Failed" };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function toast(msg) {
  toastEl.textContent = msg;
  toastEl.hidden = false;
  clearTimeout(toast.t);
  toast.t = setTimeout(() => (toastEl.hidden = true), 4000);
}

function resultCell(s) {
  if (!s.result) return "—";
  return s.result.abnormal ? `<span class="result-bad">Abnormal</span>` : `<span class="result-ok">Normal</span>`;
}

async function getMe() {
  const r = await fetch("/api/me");
  return r.ok ? r.json() : null;
}

async function logout() {
  await api("/api/auth/logout", { method: "POST" });
  location.reload();
}

function renderLogin({ role, title, altHref, altText }) {
  document.body.classList.add("logged-out");
  view.innerHTML = `<div class="card login"><h2>${title}</h2>
    <form id="login-form">
      <label>Username<input name="username" required autocomplete="username" /></label>
      <label>Password<input name="password" type="password" required autocomplete="current-password" /></label>
      <div id="login-error" class="login-error" hidden></div>
      <button class="btn" type="submit">Log in</button>
    </form>
    <a class="alt-login" href="${altHref}">${altText}</a></div>`;
  document.getElementById("login-form").onsubmit = async (e) => {
    e.preventDefault();
    const f = new FormData(e.target);
    try {
      await api("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username: f.get("username"), password: f.get("password"), role }),
      });
      location.reload();
    } catch (err) {
      const el = document.getElementById("login-error");
      el.textContent = err.message;
      el.hidden = false;
    }
  };
}
