//this is the main file, it doesnt know how the sensor, the battery or the bluetooth work, it only connects the other files together
//  config.h       -> all the settings and pins
//  payload.h      -> the packet that gets sent (and the flags)
//  ring_buffer    -> the buffer that holds the packets while the phone is not connected
//  heart_sensor   -> the MAX30102 and the beat detection
//  power          -> battery percentage and usb detection
//  ble_service    -> everything bluetooth (NimBLE)
//  debug_print    -> the printing on the serial monitor

#include <Wire.h>

#include "config.h"
#include "payload.h"
#include "ring_buffer.h"
#include "heart_sensor.h"
#include "power.h"
#include "ble_service.h"
#include "debug_print.h"

unsigned long now;
unsigned long lastTime = 0;

//these are the only global variables of the project, every other file keeps its own stuff private
Payload payload;
HeartMeasurements heart_mes;
MP_RingBuffer rb;

//this file puts the measurements of the different modules together in the payload
void acquireMeasurement(Payload *payload, HeartMeasurements *heart_mes) {
#if SIMULATE_SENSOR
  getHeart(heart_mes);
#endif

  payload->sequence++;
  payload->uptime_ms = millis();
  payload->heart_bpm = heart_mes->beatsPerMinute;
  payload->heart_IrValue = heart_mes->irValue;
  payload->heart_redValue = heart_mes->redValue;
  payload->heart_lastBeat = heart_mes->lastBeat;
  payload->batt_lvl = readBatteryPercentage();
}

uint32_t sentTotal = 0;

//takes the oldest packet from the buffer and gives it to the bluetooth module, one packet each time it is called
void pumpTx() {
  static uint32_t lastTx = 0;

  //if not connected or notification not enabled
  if (!bleCanSend()) {
    return;
  }

  //if the buffer is empty
  if (MP_rb_isEmpty(&rb)) {
    return;
  }

  //if not enough time passed
  if (millis() - lastTx < 20) {
    return;
  }

  //index of the oldest packet
  const Payload *pkt = MP_rb_peek(&rb);

  //the packet is only removed from the buffer if the bluetooth stack accepted it, if not it stays there and we try again next time
  if (bleSend((const uint8_t*)pkt, sizeof(Payload))) {
    MP_rb_drop(&rb);
    sentTotal++;
  }

  lastTx = millis();
}

void setup() {
  MP_rb_init(&rb);

  //clear flags
  payload.flags = 0;

  //set simulation flag
  if (SIMULATE_SENSOR) {
    payload.flags |= FLAG_SIMULATION_MODE;
  }

  // Derive 16-bit device ID from ESP32 eFuse MAC address
  uint64_t mac = ESP.getEfuseMac();
  payload.device_id = (uint16_t)(mac >> 32);

  //battery and usb sense setup
  initPower();

  //Serial communication
  Serial.begin(115200);
  unsigned long serialStart = millis();
  while (!Serial && (millis() - serialStart < 1500)) {
    delay(10);
  }

  Wire.begin(I2C_SDA_PIN, I2C_SCL_PIN);

  //Init and start  Bluetooth
  if (!initNimBLE(UNIT_NAME)) {
    payload.flags |= FLAG_BLE_ERROR;
  } else {
    payload.flags &= ~FLAG_BLE_ERROR;
  }

#if !SIMULATE_SENSOR
  //MAX30102 Init and Start
  if (!initHeartSensor()) {
    payload.flags |= FLAG_SENSOR_ERROR;
  } else {
    payload.flags &= ~FLAG_SENSOR_ERROR;
  }
#endif
}

void loop() {
#if !SIMULATE_SENSOR
  static bool firstRead = true;
  if (heartSensorReady() && firstRead) {
    Serial.println("Starting first MAX30102 reading...");
    Serial.flush();
  }
  getHeart(&heart_mes);
  if (heartSensorReady() && firstRead) {
    Serial.println("First MAX30102 reading returned.");
    Serial.flush();
    firstRead = false;
  }
#endif

  pumpTx();

  now = millis();

  if (now - lastTime >= SAMPLE_TIME_MS) {
    now = millis();
    lastTime = now;

    acquireMeasurement(&payload, &heart_mes);

    //send battery
    bleSendBattery(payload.batt_lvl);

    //the heart module only says if the contact is poor, here it becomes a flag
    if (heart_mes.poorContact) {
      payload.flags |= FLAG_POOR_WRIST_CONTACT;
    } else {
      payload.flags &= ~FLAG_POOR_WRIST_CONTACT;
    }

    //checks if usb-c is connected
    if (isUsbCharging()) {
      payload.flags |= FLAG_BATTERY_CHARGING;
    } else {
      payload.flags &= ~FLAG_BATTERY_CHARGING;
    }

    if (payload.batt_lvl < 20) {
      payload.flags |= FLAG_LOW_BATTERY;
    } else {
      payload.flags &= ~FLAG_LOW_BATTERY;
    }

    //buffering
    if (MP_rb_isFull(&rb)) {
      payload.flags |= FLAG_BUFFER_OVERFLOW;
    } else {
      payload.flags &= ~FLAG_BUFFER_OVERFLOW;
    }

    Payload p = payload;

    if (!bleIsConnected()) {
      p.flags |= FLAG_OFFLINE_BUFFERING;
    }

    //push in the buffer
    MP_rb_push(&rb, p);

    //print p (the copy that goes in the buffer) so the flags shown are the ones that are sent
    printPayload(&p, bleIsConnected(), rb.count, sentTotal);
  }
}
