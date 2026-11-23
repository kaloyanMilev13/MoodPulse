#include <Arduino.h>

#include "payload.h"
#include "debug_print.h"

void printPayload(const Payload *p, bool bleConnected, size_t bufferedPackets, uint32_t sentPackets) {
  // Print to Serial Monitor for local debugging
  Serial.println();
  Serial.println(F("========== MoodPulse payload =========="));

  Serial.print(F("Device ID:       "));
  Serial.print(p->device_id);
  Serial.print(F(" (0x"));
  Serial.print(p->device_id, HEX);
  Serial.println(F(")"));

  Serial.print(F("Sequence:        "));
  Serial.println(p->sequence);

  Serial.print(F("Uptime:          "));
  Serial.print(p->uptime_ms);
  Serial.println(F(" ms"));

  Serial.print(F("Heart rate:      "));
  Serial.print(p->heart_bpm);
  Serial.println(F(" BPM"));

  Serial.print(F("Last beat time:  "));
  Serial.print(p->heart_lastBeat);
  Serial.println(F(" ms since boot"));

  Serial.print(F("IR reading:      "));
  Serial.println(p->heart_IrValue);

  Serial.print(F("Red reading:     "));
  Serial.println(p->heart_redValue);

  Serial.print(F("Battery:         "));
  Serial.print(p->batt_lvl);
  Serial.println(F("%"));

  Serial.print(F("Status Flags (0x"));
  if (p->flags < 16) Serial.print("0");
  Serial.print(p->flags, HEX);
  Serial.println(F("):"));

  Serial.print(F("  - BLE Error:          ")); Serial.println((p->flags & FLAG_BLE_ERROR) ? "YES" : "NO");
  Serial.print(F("  - Sensor Error:       ")); Serial.println((p->flags & FLAG_SENSOR_ERROR) ? "YES" : "NO");
  Serial.print(F("  - Buffer Overflow:    ")); Serial.println((p->flags & FLAG_BUFFER_OVERFLOW) ? "YES" : "NO");
  Serial.print(F("  - Low Battery:        ")); Serial.println((p->flags & FLAG_LOW_BATTERY) ? "YES" : "NO");
  Serial.print(F("  - Poor Wrist Contact: ")); Serial.println((p->flags & FLAG_POOR_WRIST_CONTACT) ? "YES" : "NO");
  Serial.print(F("  - Offline Buffering:  ")); Serial.println((p->flags & FLAG_OFFLINE_BUFFERING) ? "YES" : "NO");
  Serial.print(F("  - Simulation Mode:    ")); Serial.println((p->flags & FLAG_SIMULATION_MODE) ? "YES" : "NO");
  Serial.print(F("  - Battery Charging:   ")); Serial.println((p->flags & FLAG_BATTERY_CHARGING) ? "YES" : "NO");

  Serial.print(F("BLE connected:   "));
  Serial.println(bleConnected ? F("yes") : F("no"));

  Serial.print(F("Payload size:    "));
  Serial.print(sizeof(Payload));
  Serial.println(F(" bytes"));

  Serial.print(F("Buffered packets: ")); Serial.println(bufferedPackets);
  Serial.print(F("Sent packets:     ")); Serial.println(sentPackets);

  Serial.println(F("======================================="));
}
