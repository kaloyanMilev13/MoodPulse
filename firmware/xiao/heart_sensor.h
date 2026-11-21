#pragma once

//everything about the MAX30102 is in this module: starting it, reading it and finding the beats
//the sensor object itself is private to heart_sensor.cpp, the other files only see these functions
//this module never touches the payload, it only fills HeartMeasurements and the main file decides what to do with it

typedef struct {
  float beatsPerMinute;
  unsigned long lastBeat;
  long irValue;
  long redValue;
  bool poorContact; //true when the ir value is too low, so the main file can set FLAG_POOR_WRIST_CONTACT
} HeartMeasurements;

bool initHeartSensor();      //returns false if the sensor was not found
bool heartSensorReady();     //true if initHeartSensor worked
void getHeart(HeartMeasurements *heart_mes);
