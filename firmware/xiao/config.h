#pragma once

//this file has all the settings in one place, so if you need to change a pin or a timing you only look here
//every .cpp file includes it, because things like SIMULATE_SENSOR have to have the same value in every file

// ==========================================
// XIAO ESP32-C3 Hardware Pin Configuration
// ==========================================

#define I2C_SDA_PIN 6
#define I2C_SCL_PIN 7

// Optional external battery voltage divider on an ADC pin
// Set to -1 if not connected; keep divided voltage <= 3.3V!
#define VBAT_ADC_PIN -1
#define VBAT_DIV_RATIO 2.0f // e.g., 2.0f for equal R1 and R2 resistors

// Optional USB VBUS sense pin (with voltage divider/series resistor <= 3.3V).
// Set to -1 if not connected.
#define VBUS_SENSE_PIN -1

//how often a new payload is made and pushed in the buffer, the sensor itself is read much faster than this
#define SAMPLE_TIME_MS 1000

//true = fake bpm that goes from 40 to 180, so you can test the bluetooth and the backend without the sensor
#define SIMULATE_SENSOR false

//how many payloads the ring buffer can hold, with 1 payload per second this is 10 seconds of data while the phone is not connected
#define RING_CAPACITY 10
