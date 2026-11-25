//the .ino file gets this include for free, but .cpp files dont, so every .cpp has to include it to use millis(), Serial and so on
#include <Arduino.h>
#include <Wire.h>

#include <MAX30105.h>
#include "heartRate.h" //algorithm library for checkForBeat()

#include "config.h"
#include "heart_sensor.h"

//static means only this file can see them, so nobody else can change them by accident
static MAX30105 particleSensor;
static bool sensorReady = false;

bool initHeartSensor() {
  //MAX30102 Init and Start
  //Wire.begin() is not here, it is in setup() because the i2c bus belongs to the whole project and not only to this sensor
  if (particleSensor.begin(Wire, I2C_SPEED_FAST) == false) {
    return false;
  }

  //particleSensor.setup();
  particleSensor.setup(0x1F, 4, 2, 400, 411, 4096); //red + IR mode 
  sensorReady = true;
  return true;
}

bool heartSensorReady() {
  return sensorReady;
}

void getHeart(HeartMeasurements *heart_mes) {

  static float beatsPerMinute = 40;

#if SIMULATE_SENSOR
  if (beatsPerMinute >= 180) {
    beatsPerMinute = 40;
  }
  beatsPerMinute++;
#else
  if (!sensorReady) return;

  heart_mes->irValue = particleSensor.getIR();
  heart_mes->redValue = particleSensor.getRed();

  //before this module set the flag in the payload itself, now it only says if the contact is poor and the main file makes the flag
  if (heart_mes->irValue < 30000) {
    heart_mes->poorContact = true;
    heart_mes->beatsPerMinute = 0;
    heart_mes->lastBeat = 0; //forget the old beat
    return;
  } else {
    heart_mes->poorContact = false;
  }

  if (checkForBeat(heart_mes->irValue)) {
    unsigned long beatTime = millis();
    unsigned long delta = beatTime - heart_mes->lastBeat;

    if (heart_mes->lastBeat != 0 && delta > 0) {
      float bpm = 60000.0f / delta;
      if (bpm > 0 && bpm < 255) {
        heart_mes->beatsPerMinute = bpm; //raw value of the single beat
      }
    }
    heart_mes->lastBeat = beatTime;
  }
#endif

#if SIMULATE_SENSOR
  if (beatsPerMinute <= 255 && beatsPerMinute > 0) {
    heart_mes->beatsPerMinute = beatsPerMinute;
  }
#endif
}
