#include <Adafruit_TinyUSB.h>

#define SAMPLE_TIME_MS 10000

unsigned int now;
unsigned int lastTime = 0;

void setup() {
  Serial.begin(115200);
}

void loop() {

  now = millis();

  if(now - lastTime >= SAMPLE_TIME_MS){

    now = millis();
    lastTime = now;

    Serial.println("Hello from MoodPulse!");
  }

}
