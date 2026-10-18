// Simulated ESP32 that stores readings until the website acknowledges them.
// A real device would implement the same interface over Web Bluetooth (see README "Bluetooth sync").
class SimulatedBluetoothDevice {
  static async connect() {
    const r = await fetch("/api/simulated-device/buffer");
    if (!r.ok) throw new Error("Could not connect to device");
    const { device_id, readings } = await r.json();
    return new SimulatedBluetoothDevice(device_id, readings);
  }

  constructor(deviceId, readings) {
    this.deviceId = deviceId;
    this.readings = readings;
    this.storageKey = `mirra.esp32.${deviceId}.confirmed`;
    this.confirmed = new Set(JSON.parse(localStorage.getItem(this.storageKey) || "[]"));
  }

  static key(r) {
    return `${r.session_id}:${r.seq}`;
  }

  unsynced() {
    return this.readings.filter((r) => !this.confirmed.has(SimulatedBluetoothDevice.key(r)));
  }

  confirm(acks) {
    acks.forEach((a) => this.confirmed.add(SimulatedBluetoothDevice.key(a)));
    localStorage.setItem(this.storageKey, JSON.stringify([...this.confirmed]));
  }
}

const SYNC_CHUNK_SIZE = 5;

// Sends unsynced readings in chunks; the device drops a reading only once the website has acked it,
// so an interrupted sync resumes on the next attempt without losing or double-counting readings.
async function syncDevice(device) {
  const pending = device.unsynced();
  const totals = { accepted: 0, duplicates: 0 };
  for (let i = 0; i < pending.length; i += SYNC_CHUNK_SIZE) {
    const res = await api("/api/device/sync", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ device_id: device.deviceId, readings: pending.slice(i, i + SYNC_CHUNK_SIZE) }),
    });
    totals.accepted += res.accepted;
    totals.duplicates += res.duplicates;
    device.confirm(res.acks);
  }
  return totals;
}
