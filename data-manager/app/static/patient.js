let me;
let device;

function readingsTable(scans) {
  if (!scans.length) return `<div class="empty">No readings yet. Sync your device to send its readings.</div>`;
  return `<table><thead><tr><th>Recorded</th><th>Temp (°C)</th><th>Status</th><th>Result</th></tr></thead><tbody>
    ${scans
      .map(
        (s) => `<tr><td>${fmtDate(s.recorded_at)}</td><td>${s.temperature_c.toFixed(2)}</td>
        <td><span class="pill ${s.status}">${statusText[s.status] || s.status}</span></td><td>${resultCell(s)}</td></tr>`
      )
      .join("")}</tbody></table>`;
}

function diagnosisCard(scans) {
  const latest = scans.find((s) => s.result);
  if (!latest) return `<div class="card diagnosis"><div class="label">My diagnosis</div><div class="empty">No results yet.</div></div>`;
  const r = latest.result;
  return `<div class="card diagnosis"><div class="label">My diagnosis</div>
    <div class="${r.abnormal ? "result-bad" : "result-ok"} diagnosis-label">${esc(r.label)}</div>
    <div class="diagnosis-meta">Latest reading ${latest.temperature_c.toFixed(2)} °C on ${fmtDate(latest.recorded_at)}
      (threshold ${r.threshold_c} °C)</div>
    <p class="disclaimer">Demo only — synthetic data and a placeholder threshold model, not a medical device.</p></div>`;
}

async function render() {
  const scans = await api("/api/me/scans");
  if (me.device_id && !device) device = await SimulatedBluetoothDevice.connect();
  const unsynced = device ? device.unsynced().length : 0;
  view.innerHTML = `<h2>Hi, ${esc(me.username)} <span class="patient-uid">Patient UID ${esc(me.patient_uid)}</span></h2>
    <div class="card stat-row"><div class="label">My device</div>
      ${
        me.device_id
          ? `<div class="device-name"><b>${esc(me.device_id)}</b>Connected via Bluetooth</div>
             <div class="stat"><b id="unsynced">${unsynced}</b><span>Unsynced readings on device</span></div>
             <button class="btn" id="sync">Sync</button>`
          : `<div class="device-name">No device connected yet.</div><button class="btn" id="connect">Connect device</button>`
      }</div>
    ${diagnosisCard(scans)}
    <h2>My readings</h2>
    <div class="card">${readingsTable(scans)}</div>`;
  document.getElementById("sync")?.addEventListener("click", sync);
  document.getElementById("connect")?.addEventListener("click", connect);
  return scans;
}

async function connect() {
  try {
    device = await SimulatedBluetoothDevice.connect();
    await api("/api/me/device", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ device_id: device.deviceId }),
    });
    me.device_id = device.deviceId;
    toast(`Connected to ${device.deviceId}.`);
    await render();
  } catch (e) {
    device = undefined;
    toast(`Could not connect: ${e.message}`);
  }
}

async function sync() {
  if (!device.unsynced().length) return toast("Your device is up to date.");
  try {
    const r = await syncDevice(device);
    toast(`Synced ${r.accepted} reading(s). Analyzing…`);
  } catch (e) {
    toast(`Sync stopped: ${e.message}. Unsynced readings stay on your device; sync again to resume.`);
  }
  for (let i = 0; i < 30; i++) {
    const scans = await render();
    if (!scans.some((s) => s.status === "uploaded" || s.status === "analyzing")) return;
    await sleep(1000);
  }
}

document.getElementById("logout").onclick = logout;
(async () => {
  me = await getMe();
  if (me?.role !== "patient") {
    return renderLogin({ role: "patient", title: "Patient login", altHref: "/admin", altText: "Clinic staff? Admin login →" });
  }
  try {
    await render();
  } catch (e) {
    view.innerHTML = `<div class="card empty">Error: ${esc(e.message)}</div>`;
  }
})();
