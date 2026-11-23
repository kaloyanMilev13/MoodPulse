#include <Arduino.h>

#include "config.h"
#include "power.h"

void initPower() {
  //battery reading setup
#if VBAT_ADC_PIN >= 0
  analogReadResolution(12); // 4096 ADC steps
  analogSetPinAttenuation(VBAT_ADC_PIN, ADC_11db);
  delay(10);
#endif

#if VBUS_SENSE_PIN >= 0
  pinMode(VBUS_SENSE_PIN, INPUT);
#endif
}

uint8_t readBatteryPercentage() {
#if VBAT_ADC_PIN >= 0
  analogReadMilliVolts(VBAT_ADC_PIN); //it is suggested to discard the first reading

  uint32_t totalMv = 0;
  for (int i = 0; i < 16; i++) { // get 16 readings to average them to reduce noise
    totalMv += analogReadMilliVolts(VBAT_ADC_PIN);
  }

  float pinVolts = (totalMv / 16.0f) / 1000.0f; //get the average
  float battVolts = pinVolts * VBAT_DIV_RATIO;

  //rough estimate for 3.7v LiPo
  float percent = (battVolts - 3.3f) * 100.0f / (4.2f - 3.3f);

  //convert to %
  percent = constrain(percent, 0.0f, 100.0f);
  return (uint8_t)(percent + 0.5f); //why the +0.5f?
#else
  //no battery pin configured, so we just say 100
  return 100;
#endif
}

bool isUsbCharging() {
#if VBUS_SENSE_PIN >= 0
  return digitalRead(VBUS_SENSE_PIN) == HIGH;
#else
  //no sense pin configured, so we cant know
  return false;
#endif
}
