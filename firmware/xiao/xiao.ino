#include <Adafruit_TinyUSB.h>

void setup() {
  Serial.begin(115200);
}

void loop() {
  Serial.println("Hello from MoodPulse!");
  delay(1000);
}
