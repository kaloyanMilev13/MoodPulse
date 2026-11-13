"use strict";

const UART_SERVICE = "6e400001-b5a3-f393-e0a9-e50e24dcca9e";
const UART_TX = "6e400003-b5a3-f393-e0a9-e50e24dcca9e";

const PACKET_SIZE = 26; // can change in the future

// sensors endpoint, shows readings in a json
const SENSOR_URL = "/api/sensors";

const connectButton = document.getElementById("connect-bracelet");
const disconnectButton = document.getElementById("disconnect-bracelet");
const startReadingsButton = document.getElementById("start-bracelet-btn");
const stopReadingsButton = document.getElementById("stop-bracelet-btn");
const allowTestCheckbox = document.getElementById("allow-bracelet-test");
const statusElement = document.getElementById("bracelet-status");

let braceletDevice = null;
let txCharacteristic = null;
let pendingBytes = new Uint8Array(0);
let connecting = false;
let readingActive = false;
let changingReadingState = false;

function setBraceletStatus(message) {
  statusElement.textContent = message;
}

function updateButtons() {
  const connected = Boolean(braceletDevice?.gatt?.connected);
  connectButton.disabled = connecting || connected;
  disconnectButton.disabled = !connected;
  startReadingsButton.disabled = !connected || readingActive || changingReadingState;
  stopReadingsButton.disabled = !connected || !readingActive || changingReadingState;
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
  pendingBytes = new Uint8Array(0);
  updateButtons();
}

function onBraceletDisconnected() {
  resetConnection();
  void setBackendReadingState(false).catch(error => {
    console.error("Could not stop backend sensor session:", error);
  });
  setBraceletStatus("Bracelet disconnected.");
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
    pendingBytes = new Uint8Array(0);

    setBraceletStatus("Bracelet connected. Start bracelet readings when ready.");
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

async function startBraceletReadings() {
  if (!txCharacteristic || !braceletDevice?.gatt?.connected || changingReadingState) return;
  changingReadingState = true;
  updateButtons();
  try {
    await setBackendReadingState(true);
    readingActive = true;
    txCharacteristic.addEventListener("characteristicvaluechanged", receiveReading);
    await txCharacteristic.startNotifications();
    setBraceletStatus("Bracelet readings active. This is separate from the camera session.");
  } catch (error) {
    readingActive = false;
    txCharacteristic?.removeEventListener("characteristicvaluechanged", receiveReading);
    try {
      await setBackendReadingState(false);
    } catch (rollbackError) {
      console.error("Could not stop backend sensor session:", rollbackError);
    }
    console.error("Could not start bracelet readings:", error);
    setBraceletStatus(`Could not start bracelet readings: ${error.message}`);
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
    await setBackendReadingState(false);
    readingActive = false;
    if (txCharacteristic) {
      txCharacteristic.removeEventListener("characteristicvaluechanged", receiveReading);
    }
    pendingBytes = new Uint8Array(0);
    if (txCharacteristic) {
      await txCharacteristic.stopNotifications();
    }
    setBraceletStatus("Bracelet readings stopped. The camera session is unchanged.");
  } catch (error) {
    console.error("Could not stop bracelet readings:", error);
    setBraceletStatus(`Could not stop bracelet readings: ${error.message}`);
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
    isTest: view.getUint8(24),
    flags: view.getUint8(25),
    receivedAt: Date.now() / 1000
  };
}

function receiveReading(event) {
  if (!readingActive) return;
  const value = event.target.value;

  const incoming = new Uint8Array(
    value.buffer,
    value.byteOffset,
    value.byteLength
  );

  const combined = new Uint8Array(
    pendingBytes.length + incoming.length
  );

  combined.set(pendingBytes);
  combined.set(incoming, pendingBytes.length);
  pendingBytes = combined;

  while (pendingBytes.length >= PACKET_SIZE) {
    const packet = pendingBytes.slice(0, PACKET_SIZE);
    pendingBytes = pendingBytes.slice(PACKET_SIZE);

    const reading = decodePacket(packet);

    console.log("Bracelet reading:", reading);
    void sendReading(reading);
  }
}

async function sendReading(reading) {
  const { sequence, heartBpm, flags, isTest, receivedAt } = reading;

  if (flags !== 0) {
    setBraceletStatus(`Bracelet reported an error (flag ${flags}); reading skipped.`);
    return;
  }

  if (isTest !== 0 && isTest !== 1) {
    setBraceletStatus(`Skipped unknown test flag: ${isTest}`);
    return;
  }

  if (isTest === 1 && !allowTestCheckbox.checked) {
    setBraceletStatus(
      "Test reading skipped. Enable simulated readings for demo testing."
    );
    return;
  }

  if (heartBpm < 30 || heartBpm > 220) {
    setBraceletStatus(`Skipped invalid heart rate: ${heartBpm}`);
    return;
  }

  try {
    const response = await fetch(SENSOR_URL, {
      method: "POST",
      headers: {
        "Content-Type": "application/json"
      },
      body: JSON.stringify({
        readings: [
          {
            type: "heart_rate",
            value: heartBpm,
            ts: receivedAt
          }
        ]
      })
    });

    if (response.status === 409) {
      setBraceletStatus(
        "Start bracelet readings in the dashboard before sending sensor data."
      );
      return;
    }

    if (response.status !== 201) {
      const body = await response.text();
      console.error("Backend rejected reading:", response.status, body);

      setBraceletStatus(
        `Reading not saved: HTTP ${response.status}. See browser console.`
      );
      return;
    }

    const label = isTest === 1 ? "SIMULATED" : "Device";

    setBraceletStatus(
      `${label} reading saved: ${heartBpm} BPM — sequence ${sequence}`
    );
  } catch (error) {
    console.error("Saving bracelet reading failed:", error);
    setBraceletStatus(
      "Could not reach Flask. Check that the backend is running."
    );
  }
}

connectButton.addEventListener("click", connectBracelet);
disconnectButton.addEventListener("click", disconnectBracelet);
startReadingsButton.addEventListener("click", startBraceletReadings);
stopReadingsButton.addEventListener("click", stopBraceletReadings);
updateButtons();

window.addEventListener("pagehide", () => {
  if (readingActive) {
    navigator.sendBeacon("/api/sensor-session",
      new Blob([JSON.stringify({ active: false })], { type: "application/json" }));
  }
});
