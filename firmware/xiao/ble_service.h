#pragma once

#include <stdint.h>
#include <stddef.h>

//everything bluetooth is in this module (NimBLE), the other files never see a NimBLE type
//the idea is that if you ever change the bluetooth library only ble_service.cpp has to change, and these functions stay the same
//the connection state is kept inside ble_service.cpp, the callbacks update it and these functions let the rest of the code read it

bool initNimBLE(const char* deviceName); //returns false if the server or a service could not be created
bool bleIsConnected();                   //a phone is connected
bool bleCanSend();                       //connected AND the phone enabled notifications (the same as bleuart.notifyEnabled() on the nrf52)
bool bleSend(const uint8_t *data, size_t len); //puts the data in the stack, returns true if the stack accepted it
void bleSendBattery(uint8_t percent);    //updates the battery level and notifies the phone, does nothing if no phone is connected
