const view = document.getElementById("view");
const toastEl = document.getElementById("toast");

const api = async (path, opts) => {
  const r = await fetch(path, opts);
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
  return r.json();
};

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmtDate = (iso) => (iso ? new Date(iso).toLocaleString([], { dateStyle: "medium", timeStyle: "short" }) : "—");
const statusText = { uploaded: "Uploaded", analyzing: "Analyzing", results_ready: "Results ready", failed: "Failed" };

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

function scanTable(scans) {
  if (!scans.length) return `<div class="empty">No scans yet. Click <b>Sync from device</b> to receive readings from the ESP32.</div>`;
  return `<table><thead><tr><th>Patient UID</th><th>Device / session</th><th>Recorded</th><th>Temp (°C)</th><th>Status</th><th>Result</th></tr></thead><tbody>
    ${scans
      .map(
        (s) => `<tr data-id="${s.id}"><td><b>${esc(s.patient_uid)}</b></td><td>${esc(s.device_id)} / ${esc(s.session_id)}</td><td>${fmtDate(s.recorded_at)}</td>
        <td>${s.temperature_c.toFixed(2)}</td><td><span class="pill ${s.status}">${statusText[s.status] || s.status}</span></td><td>${resultCell(s)}</td></tr>`
      )
      .join("")}</tbody></table>`;
}

function bindRows() {
  view.querySelectorAll("tbody tr[data-id]").forEach((tr) => (tr.onclick = () => (location.hash = `#/scans/${tr.dataset.id}`)));
}

let device;
const getDevice = async () => (device ??= await SimulatedBluetoothDevice.connect());

async function renderDashboard() {
  const [s, scans, dev] = await Promise.all([api("/api/summary"), api("/api/scans"), getDevice()]);
  const server = await api(`/api/devices/${encodeURIComponent(dev.deviceId)}`);
  const pending = dev.unconfirmed().length;
  view.innerHTML = `
    <div class="card stat-row"><div class="label">Device</div>
      <div class="device-name"><b>${esc(dev.deviceId)}</b>Simulated Bluetooth<br />Last sync: ${fmtDate(server.last_synced_at)}</div>
      <div class="stat"><b>${dev.allReadings().length}</b><span>Stored on device</span></div>
      <div class="stat ${pending ? "bad" : ""}"><b>${pending}</b><span>Awaiting confirmation</span></div>
      <div class="device-actions">
        <button class="btn" id="dash-sync">Sync from device</button>
        <div class="row"><button class="link-btn" id="record">Record new reading</button>
          <button class="link-btn" id="reset-device" title="Forget which readings were confirmed, so the device resends everything">Reset device memory</button></div>
        <label><input type="checkbox" id="simulate-drop" ${simulateDrop ? "checked" : ""} /> Simulate connection drop</label>
      </div></div>
    <div class="card stat-row"><div class="label">Scan</div>
      <div class="stat"><b>${s.patients}</b><span>Patients</span></div>
      <div class="stat"><b>${s.uploaded + s.analyzing + s.results_ready + s.failed}</b><span>Readings received</span></div></div>
    <div class="card stat-row"><div class="label">Analyze</div>
      <div class="stat"><b>${s.uploaded}</b><span>Queued</span></div>
      <div class="stat"><b>${s.analyzing}</b><span>Analyzing</span></div>
      <div class="stat ${s.failed ? "bad" : ""}"><b>${s.failed}</b><span>Failed</span></div></div>
    <div class="card stat-row"><div class="label">Results</div>
      <div class="stat"><b>${s.results_ready}</b><span>Results ready</span></div>
      <div class="stat bad"><b>${s.abnormal}</b><span>Abnormal</span></div>
      <a class="btn outline" href="#/scans?status=results_ready" style="text-decoration:none">View results</a></div>
    <h2>Recent scans</h2>
    <div class="card">${scanTable(scans.slice(0, 8))}</div>`;
  document.getElementById("dash-sync").onclick = sync;
  document.getElementById("simulate-drop").onchange = (e) => (simulateDrop = e.target.checked);
  document.getElementById("record").onclick = () => {
    const r = dev.recordReading();
    toast(`Device recorded ${r.session_id} (${r.temperature_c.toFixed(2)} °C). Not yet synced.`);
    route();
  };
  document.getElementById("reset-device").onclick = () => {
    dev.resetMemory();
    toast("Device memory reset: all readings will be resent (the website ignores duplicates).");
    route();
  };
  bindRows();
}

async function renderScans(params) {
  const status = params.get("status") || "";
  const q = params.get("q") || "";
  const qs = new URLSearchParams();
  if (status) qs.set("status", status);
  if (q) qs.set("q", q);
  const scans = await api(`/api/scans?${qs}`);
  const tab = (v, label) => `<a href="#/scans${v ? `?status=${v}` : ""}" class="${status === v ? "active" : ""}">${label}</a>`;
  view.innerHTML = `<h2>All scans${q ? ` matching “${esc(q)}”` : ""}</h2>
    <div class="card"><div class="tabs">${tab("", "All")}${tab("uploaded", "Queued")}${tab("analyzing", "Analyzing")}${tab("results_ready", "Results")}${tab("failed", "Failed")}</div>
    ${scanTable(scans)}</div>`;
  bindRows();
}

async function renderScan(id) {
  const s = await api(`/api/scans/${id}`);
  const r = s.result;
  const lo = 32, hi = 37.5;
  const pct = (t) => Math.max(0, Math.min(100, ((t - lo) / (hi - lo)) * 100));
  view.innerHTML = `<a class="back" href="#/scans">← All scans</a>
    <div class="detail">
      <div class="card"><h2>UID: ${esc(s.patient_uid)}</h2>
        <dl class="kv"><dt>Patch size</dt><dd>${s.patch_size}</dd><dt>Device ID</dt><dd>${esc(s.device_id)}</dd>
        <dt>Session / seq</dt><dd>${esc(s.session_id)} / ${s.seq}</dd><dt>Recorded</dt><dd>${fmtDate(s.recorded_at)}</dd><dt>Uploaded</dt><dd>${fmtDate(s.uploaded_at)}</dd>
        <dt>Status</dt><dd><span class="pill ${s.status}">${statusText[s.status] || s.status}</span></dd></dl></div>
      <div class="card"><div style="color:var(--muted);font-size:13px">Breast skin temperature</div>
        <div class="big-temp">${s.temperature_c.toFixed(2)} °C</div>
        ${
          r
            ? `<div class="gauge"><div class="thresh" style="left:${pct(r.threshold_c)}%"><span>threshold ${r.threshold_c} °C</span></div>
                 <div class="marker" style="left:calc(${pct(s.temperature_c)}% - 2px)"></div></div>
               <div class="${r.abnormal ? "result-bad" : "result-ok"}" style="font-size:20px">${esc(r.label)}</div>
               <dl class="kv" style="margin-top:12px"><dt>Model</dt><dd>${esc(r.model_version)}</dd><dt>Analyzed</dt><dd>${fmtDate(r.analyzed_at)}</dd></dl>`
            : `<div class="empty">No result yet.</div>`
        }
        <button class="btn outline" id="reanalyze" style="margin-top:16px">Re-run analysis</button>
        <p class="disclaimer">Demo only — synthetic data and a placeholder threshold model, not a medical device.</p></div>
    </div>`;
  document.getElementById("reanalyze").onclick = async () => {
    await api(`/api/scans/${id}/analyze`, { method: "POST" });
    toast("Analysis queued");
    setTimeout(route, 800);
  };
}

async function renderPatients() {
  const ps = await api("/api/patients");
  view.innerHTML = `<h2>Patients</h2><div class="card">${
    ps.length
      ? `<table><thead><tr><th>Patient UID</th><th>Patch size</th><th>Registered</th><th>Scans</th></tr></thead><tbody>
        ${ps.map((p) => `<tr data-uid="${esc(p.uid)}"><td><b>${esc(p.uid)}</b></td><td>${p.patch_size}</td><td>${fmtDate(p.registered_at)}</td><td>${p.scans}</td></tr>`).join("")}
        </tbody></table>`
      : `<div class="empty">No patients yet.</div>`
  }</div>`;
  view.querySelectorAll("tr[data-uid]").forEach((tr) => (tr.onclick = () => (location.hash = `#/scans?q=${encodeURIComponent(tr.dataset.uid)}`)));
}

let simulateDrop = false;

async function sync() {
  try {
    const dev = await getDevice();
    if (!dev.unconfirmed().length) return toast("Device is up to date: no unconfirmed readings.");
    const r = await syncDevice(dev, { simulateDrop });
    const dup = r.duplicates ? `, ${r.duplicates} duplicate(s) ignored` : "";
    const left = dev.unconfirmed().length;
    toast(
      r.dropped
        ? `Connection lost: ${r.accepted} new reading(s) saved${dup}; ${left} still awaiting confirmation. Sync again to resume.`
        : `Synced ${r.accepted} new reading(s)${dup}. Analyzing…`
    );
    pollUntilSettled();
  } catch (e) {
    toast(`Sync failed: ${e.message}`);
  }
}

async function pollUntilSettled() {
  for (let i = 0; i < 30; i++) {
    await route();
    const s = await api("/api/summary");
    if (!s.uploaded && !s.analyzing) return;
    await new Promise((r) => setTimeout(r, 1000));
  }
}

async function route() {
  const [path, query] = (location.hash.slice(1) || "/").split("?");
  const params = new URLSearchParams(query);
  const section = path.startsWith("/patients") ? "patients" : path.startsWith("/scans") ? "scans" : "dashboard";
  document.querySelectorAll("[data-nav]").forEach((a) => a.classList.toggle("active", a.dataset.nav === section));
  try {
    const m = path.match(/^\/scans\/(\d+)/);
    if (m) await renderScan(m[1]);
    else if (section === "scans") await renderScans(params);
    else if (section === "patients") await renderPatients();
    else await renderDashboard();
  } catch (e) {
    view.innerHTML = `<div class="card empty">Error: ${esc(e.message)}</div>`;
  }
}

document.getElementById("sync-btn").onclick = sync;
document.getElementById("search-form").onsubmit = (e) => {
  e.preventDefault();
  const q = document.getElementById("search").value.trim();
  location.hash = q ? `#/scans?q=${encodeURIComponent(q)}` : "#/scans";
};
document.getElementById("upload").onchange = async (e) => {
  const file = e.target.files[0];
  if (!file) return;
  const fd = new FormData();
  fd.append("file", file);
  try {
    const r = await api("/api/scans/upload", { method: "POST", body: fd });
    toast(`Imported ${r.accepted} reading(s) from ${file.name}${r.duplicates ? `, ${r.duplicates} duplicate(s) ignored` : ""}. Analyzing…`);
    pollUntilSettled();
  } catch (err) {
    toast(`Upload failed: ${err.message}`);
  }
  e.target.value = "";
};
window.addEventListener("hashchange", route);
route();
