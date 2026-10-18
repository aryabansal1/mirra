// Simulated ESP32 that stores readings until the website acknowledges them.
// A real device would implement the same interface over Web Bluetooth (see README "Bluetooth sync").
class SimulatedBluetoothDevice {
  static async connect() {
    const r = await fetch("/api/simulated-device/buffer");
    if (!r.ok) throw new Error("Could not connect to device");
    const { device_id, readings } = await r.json();
    return new SimulatedBluetoothDevice(device_id, readings);
  }

  constructor(deviceId, baseReadings) {
    this.deviceId = deviceId;
    this.baseReadings = baseReadings;
    this.storageKey = `mirra.device.${deviceId}`;
    const saved = JSON.parse(localStorage.getItem(this.storageKey) || "{}");
    this.confirmed = new Set(saved.confirmed || []);
    this.recorded = saved.recorded || [];
  }

  static key(r) {
    return `${r.session_id}:${r.seq}`;
  }

  save() {
    localStorage.setItem(this.storageKey, JSON.stringify({ confirmed: [...this.confirmed], recorded: this.recorded }));
  }

  allReadings() {
    return [...this.baseReadings, ...this.recorded];
  }

  unconfirmed() {
    return this.allReadings().filter((r) => !this.confirmed.has(SimulatedBluetoothDevice.key(r)));
  }

  confirm(acks) {
    acks.forEach((a) => this.confirmed.add(SimulatedBluetoothDevice.key(a)));
    this.save();
  }

  recordReading() {
    const seq = Math.max(0, ...this.allReadings().map((r) => r.seq)) + 1;
    const abnormal = Math.random() < 0.4;
    const temp = abnormal ? 35.2 + Math.random() * 1.2 : 33.0 + Math.random() * 1.8;
    const reading = {
      session_id: `S${String(seq).padStart(4, "0")}`,
      seq,
      recorded_at: new Date().toISOString().slice(0, 16),
      patient_uid: `0852-032-${String(seq).padStart(3, "0")}`,
      patch_size: 1 + Math.floor(Math.random() * 6),
      temperature_c: Math.round(temp * 100) / 100,
    };
    this.recorded.push(reading);
    this.save();
    return reading;
  }

  resetMemory() {
    this.confirmed.clear();
    this.save();
  }
}

const SYNC_CHUNK_SIZE = 5;

// Sends unconfirmed readings in chunks; each chunk's readings are dropped from the device only once acked.
// With simulateDrop, the second chunk reaches the server but its ack is "lost", so it is resent next sync.
async function syncDevice(device, { simulateDrop = false } = {}) {
  const pending = device.unconfirmed();
  const totals = { sent: pending.length, accepted: 0, duplicates: 0, dropped: false };
  for (let i = 0; i < pending.length; i += SYNC_CHUNK_SIZE) {
    const r = await fetch("/api/device/sync", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ device_id: device.deviceId, readings: pending.slice(i, i + SYNC_CHUNK_SIZE) }),
    });
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
    const res = await r.json();
    totals.accepted += res.accepted;
    totals.duplicates += res.duplicates;
    if (simulateDrop && i > 0) {
      totals.dropped = true;
      break;
    }
    device.confirm(res.acks);
  }
  return totals;
}
