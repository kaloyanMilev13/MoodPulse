#pragma once

#include <stdint.h>

//battery percentage and usb detection
//if VBAT_ADC_PIN or VBUS_SENSE_PIN are -1 in config.h the functions still work, they just return 100% and "not charging"

void initPower();                   //sets up the adc and the usb sense pin, only if they are configured
uint8_t readBatteryPercentage();    //0-100
bool isUsbCharging();
