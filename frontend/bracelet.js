"use strict";

const UART_SERVICE = "6e400001-b5a3-f393-e0a9-e50e24dcca9e";
const UART_TX = "6e400003-b5a3-f393-e0a9-e50e24dcca9e";

// Packed Payload layout emitted by xiao.ino
const PACKET_SIZE = 25;
const FLAG_BLE_ERROR = 0x01;
const FLAG_SENSOR_ERROR = 0x02;
const FLAG_BUFFER_OVERFLOW = 0x04;
const FLAG_LOW_BATTERY = 0x08;
const FLAG_POOR_WRIST_CONTACT = 0x10;
const FLAG_OFFLINE_BUFFERING = 0x20;
const FLAG_SIMULATION_MODE = 0x40;
const FLAG_BATTERY_CHARGING = 0x80;

// Do NOT include FLAG_BUFFER_OVERFLOW here: the firmware latches 0x04 on valid
// readings once the 64-slot ring buffer fills up.
const INVALID_READING_FLAGS = FLAG_BLE_ERROR | FLAG_SENSOR_ERROR;

const BATCH_IDLE_MS = 350;
const BUFFERED_AGE_SECONDS = 2;
const MAX_BATCH_SIZE = 500; // Matches MAX_SENSOR_BATCH in app.py

const SENSOR_URL = "/api/sensors";

const connectButton = document.getElementById("connect-bracelet");
const disconnectButton = document.getElementById("disconnect-bracelet");
const statusElement = document.getElementById("bracelet-status");

let braceletDevice = null;
let txCharacteristic = null;
let pendingReadings = [];
let preStartBuffer = []; // Holds bulk packets flushed immediately on BLE connect
let flushTimer = null;
let flushPromise = null;
let backendStopPromise = null;
let stopBackendAfterSync = false;
let connecting = false;
let readingActive = false;
let changingReadingState = false;

function setBraceletStatus(message) {
  statusElement.textContent = message;
}

function updateButtons() {
  const connected = Boolean(braceletDevice?.gatt?.connected);
  connectButton.disabled = connecting || connected;
  disconnectButton.disabled = !connected || connecting || changingReadingState;
}

function resetConnection() {
  if (txCharacteristic) {
    txCharacteristic.removeEventListener(
      "characteristicvaluechanged",
      receiveReading
    );
  }

  txCharacteristic = null;
  readingActive = false;
  preStartBuffer = [];
  updateButtons();
}

function onBraceletDisconnected() {
  const sessionNeedsStopping = readingActive || stopBackendAfterSync;
  resetConnection();
  stopBackendAfterSync = sessionNeedsStopping;
  void flushPendingReadings().then(flushed => {
    if (!flushed) {
      setBraceletStatus(pendingReadings.length
        ? `Bracelet disconnected; ${pendingReadings.length} reading(s) remain queued for retry.`
        : "Bracelet disconnected; readings synced, but the backend session could not be stopped.");
      return;
    }
    setBraceletStatus("Bracelet disconnected.");
  }).catch(error => {
    console.error("Could not finish bracelet sync:", error);
    setBraceletStatus(`Bracelet disconnected; sync failed: ${error.message}`);
  });
}

async function connectBracelet() {
  if (!window.isSecureContext || !navigator.bluetooth) {
    setBraceletStatus(
      "Bluetooth requires a supported browser and localhost or HTTPS. " +
      "Try desktop Chrome or Edge."
    );
    return;
  }

  connecting = true;
  updateButtons();

  try {
    setBraceletStatus("Choose your bracelet in the Bluetooth dialog.");

    braceletDevice = await navigator.bluetooth.requestDevice({
      filters: [{ services: [UART_SERVICE] }]
    });

    braceletDevice.addEventListener(
      "gattserverdisconnected",
      onBraceletDisconnected
    );

    const server = await braceletDevice.gatt.connect();
    const service = await server.getPrimaryService(UART_SERVICE);

    txCharacteristic = await service.getCharacteristic(UART_TX);
    preStartBuffer = [];

    // Listen before enabling the backend so packets arriving during startup are buffered.
    txCharacteristic.addEventListener("characteristicvaluechanged", receiveReading);
    await txCharacteristic.startNotifications();
    if (!await startBraceletReadings()) {
      throw new Error("The bracelet connected, but readings could not be started.");
    }
  } catch (error) {
    console.error("Bluetooth connection failed:", error);

    if (braceletDevice?.gatt?.connected) {
      braceletDevice.gatt.disconnect();
    }

    resetConnection();
    setBraceletStatus(`Connection failed: ${error.message}`);
  } finally {
    connecting = false;
    updateButtons();
  }
}

function disconnectBracelet() {
  if (readingActive) {
    void stopBraceletReadings().finally(() => {
      if (braceletDevice?.gatt?.connected) braceletDevice.gatt.disconnect();
    });
    return;
  }
  if (braceletDevice?.gatt?.connected) {
    braceletDevice.gatt.disconnect();
  }
}

async function setBackendReadingState(active) {
  const response = await fetch("/api/sensor-session", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ active })
  });
  const data = await response.json();
  if (!response.ok || data.status !== "success") {
    throw new Error(data.message || `HTTP ${response.status}`);
  }
}

async function finishBackendSessionStop() {
  if (!stopBackendAfterSync) return true;
  if (backendStopPromise) return backendStopPromise;

  backendStopPromise = (async () => {
    try {
      await setBackendReadingState(false);
      stopBackendAfterSync = false;
      return true;
    } catch (error) {
      console.error("Could not stop backend sensor session:", error);
      setBraceletStatus(`Could not stop backend sensor session: ${error.message}`);
      scheduleReadingsFlush(2000);
      return false;
    } finally {
      backendStopPromise = null;
    }
  })();
  return backendStopPromise;
}

async function startBraceletReadings() {
  if (!txCharacteristic || !braceletDevice?.gatt?.connected || changingReadingState) return false;
  changingReadingState = true;
  updateButtons();
  try {
    stopBackendAfterSync = false;
    await setBackendReadingState(true);
    readingActive = true;

    // Drain any buffered packets that arrived right after Bluetooth connected
    if (preStartBuffer.length > 0) {
      const cached = preStartBuffer.splice(0, preStartBuffer.length);
      for (const reading of cached) {
        sendReading(reading);
      }
    }

    setBraceletStatus("Bracelet connected; readings are active.");
    return true;
  } catch (error) {
    readingActive = false;
    try {
      await setBackendReadingState(false);
    } catch (rollbackError) {
      console.error("Could not stop backend sensor session:", rollbackError);
    }
    console.error("Could not start bracelet readings:", error);
    setBraceletStatus(`Could not start bracelet readings: ${error.message}`);
    return false;
  } finally {
    changingReadingState = false;
    updateButtons();
  }
}

async function stopBraceletReadings() {
  if (changingReadingState || !readingActive) return;
  changingReadingState = true;
  updateButtons();
  try {
    readingActive = false;
    preStartBuffer = [];
    stopBackendAfterSync = true;
    setBraceletStatus("Disconnecting bracelet and syncing pending readings...");
    if (!await flushPendingReadings()) {
      throw new Error(pendingReadings.length
        ? "Pending readings are queued for retry; the backend session remains active."
        : "Readings synced, but the backend session could not be stopped.");
    }
  } catch (error) {
    console.error("Could not stop bracelet readings:", error);
    setBraceletStatus(`Disconnecting bracelet; ${error.message}`);
  } finally {
    changingReadingState = false;
    updateButtons();
  }
}

function decodePacket(packet) {
  if (packet.byteLength !== PACKET_SIZE) {
    throw new Error(`Expected ${PACKET_SIZE}-byte bracelet packet`);
  }

  const view = new DataView(packet.buffer, packet.byteOffset, packet.byteLength);
  return {
    deviceId: view.getUint16(0, true),
    sequence: view.getUint32(2, true),
    uptimeMs: view.getUint32(6, true),
    heartBpm: view.getUint8(10),
    heartLastBeat: view.getUint32(11, true),
    irValue: view.getInt32(15, true),
    redValue: view.getInt32(19, true),
    battery: view.getUint8(23),
    flags: view.getUint8(24),
    receivedAt: Date.now() / 1000
  };
}

function receiveReading(event) {
  const value = event.target.value;
  if (value.byteLength !== PACKET_SIZE) {
    setBraceletStatus(
      `Bracelet packet rejected: expected ${PACKET_SIZE} bytes, received ${value.byteLength}.`
    );
    return;
  }

  try {
    const packet = new Uint8Array(
      value.buffer,
      value.byteOffset,
      value.byteLength
    );
    const reading = decodePacket(packet);
    console.log("Bracelet reading:", reading);

    if (!readingActive) {
      // Hold up to 256 packets flushed while connecting so they aren't lost
      preStartBuffer.push(reading);
      if (preStartBuffer.length > 256) {
        preStartBuffer.shift();
      }
      setBraceletStatus(
        `Connecting bracelet; capturing ${preStartBuffer.length} initial reading(s)...`
      );
    } else {
      sendReading(reading);
    }
  } catch (error) {
    setBraceletStatus(`Bracelet packet rejected: ${error.message}`);
  }
}

function sendReading(reading) {
  const { heartBpm, flags } = reading;

  // Keep finite out-of-range and flagged readings so backend analytics can
  // record and classify them.
  if (flags & FLAG_POOR_WRIST_CONTACT) {
    setBraceletStatus("Poor wrist contact; this reading will be left out.");
  } else if (flags & INVALID_READING_FLAGS) {
    setBraceletStatus(
      `Bracelet reported an invalid reading (flags 0x${flags.toString(16)}); it will be left out.`
    );
  }

  pendingReadings.push(reading);
  scheduleReadingsFlush();
}

function scheduleReadingsFlush(delay = BATCH_IDLE_MS) {
  if (flushTimer !== null) clearTimeout(flushTimer);
  flushTimer = setTimeout(() => {
    flushTimer = null;
    void flushPendingReadings();
  }, delay);
}

function makeSensorBatch(readings) {
  const newest = readings[readings.length - 1];
  return readings.map(reading => {
    const ageMs = (newest.uptimeMs - reading.uptimeMs) >>> 0;
    const timestamp = newest.receivedAt - ageMs / 1000;
    const isOfflineFlagged = (reading.flags & FLAG_OFFLINE_BUFFERING) !== 0;
    return {
      type: "heart_rate",
      value: reading.heartBpm,
      ts: timestamp,
      device_id: reading.deviceId,
      sequence: reading.sequence,
      uptime_ms: reading.uptimeMs,
      battery: reading.battery,
      flags: reading.flags,
      buffered: isOfflineFlagged || ageMs >= BUFFERED_AGE_SECONDS * 1000
    };
  });
}

async function flushPendingReadings() {
  if (flushTimer !== null) {
    clearTimeout(flushTimer);
    flushTimer = null;
  }
  if (flushPromise) {
    const previousFlushSucceeded = await flushPromise;
    if (!previousFlushSucceeded) return false;
    if (pendingReadings.length > 0) return flushPendingReadings();
    return stopBackendAfterSync ? flushPendingReadings() : true;
  }
  if (pendingReadings.length === 0) {
    return finishBackendSessionStop();
  }

  // Respect backend MAX_SENSOR_BATCH = 500
  const batch = pendingReadings.splice(0, MAX_BATCH_SIZE);
  const readings = makeSensorBatch(batch);
  let retryDelay = BATCH_IDLE_MS;
  const currentFlush = (async () => {
    try {
      const response = await fetch(SENSOR_URL, {
        method: "POST",
        headers: {
          "Content-Type": "application/json"
        },
        body: JSON.stringify({ readings })
      });
      const data = await response.json();
      if (response.status !== 201 || data.status !== "success") {
        throw new Error(data.message || `HTTP ${response.status}`);
      }
      if (!Number.isInteger(data.accepted) || data.accepted !== readings.length) {
        throw new Error(`Server accepted ${data.accepted} of ${readings.length} readings`);
      }

      const latest = batch[batch.length - 1];
      const bufferedCount = readings.filter(reading => reading.buffered).length;
      const batteryStatus = [
        `${latest.battery}% battery${latest.flags & FLAG_LOW_BATTERY ? " (low)" : ""}`,
        latest.flags & FLAG_BATTERY_CHARGING ? "charging" : null
      ].filter(Boolean).join(", ");
      const bufferedStatus = bufferedCount
        ? `; ${bufferedCount} buffered (uptime-estimated)`
        : "";
      setBraceletStatus(
        `Bracelet: saved ${data.accepted} heart-rate reading${data.accepted === 1 ? "" : "s"}`
        + `${bufferedStatus}. Latest ${latest.heartBpm} BPM — ${batteryStatus} — sequence ${latest.sequence}`
      );
      return true;
    } catch (error) {
      pendingReadings.unshift(...batch);
      retryDelay = 2000;
      console.error("Saving bracelet batch failed:", error);
      setBraceletStatus(`Could not sync ${pendingReadings.length} bracelet reading(s): ${error.message}`);
      return false;
    }
  })();
  flushPromise = currentFlush;
  const succeeded = await currentFlush;
  flushPromise = null;
  if (pendingReadings.length > 0) {
    scheduleReadingsFlush(retryDelay);
  } else if (succeeded && stopBackendAfterSync) {
    return finishBackendSessionStop();
  }
  return succeeded;
}
connectButton.addEventListener("click", connectBracelet);
disconnectButton.addEventListener("click", disconnectBracelet);
updateButtons();
window.addEventListener("pagehide", () => {
if (readingActive) {
navigator.sendBeacon("/api/sensor-session",
new Blob([JSON.stringify({ active: false })], { type: "application/json" }));
}
});
