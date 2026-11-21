#pragma once

#include <stdint.h>

//this file is the format of the data that gets sent over bluetooth, so the backend has to read it exactly like this
//if you add or change a field here the backend parser has to change too

//==============FLAGS==============
//every flag is one bit of the flags byte, you set it with |= and clear it with &= ~
#define FLAG_BLE_ERROR          (1 << 0) // 0x01
#define FLAG_SENSOR_ERROR       (1 << 1) // 0x02
#define FLAG_BUFFER_OVERFLOW    (1 << 2) // 0x04
#define FLAG_LOW_BATTERY        (1 << 3) // 0x08
#define FLAG_POOR_WRIST_CONTACT (1 << 4) // 0x10
#define FLAG_OFFLINE_BUFFERING  (1 << 5) // 0x20
#define FLAG_SIMULATION_MODE    (1 << 6) // 0x40
#define FLAG_BATTERY_CHARGING   (1 << 7) // 0x80

typedef struct __attribute__((__packed__)) { //this is so that the compiler doesnt add any padding bytes
  uint16_t device_id;      //2 bytes -> id of the bracelet (can be 1 byte if not enough space)
  uint32_t sequence;       //4 bytes -> number of reading
  uint32_t uptime_ms;      //4 bytes -> timestamp
  uint8_t  heart_bpm;      //1 byte -> bpm reading
  uint32_t heart_lastBeat; // 4 bytes -> time of the last heartbeat
  int32_t  heart_IrValue;  //4 bytes
  int32_t  heart_redValue; //4 bytes
  uint8_t  batt_lvl;       //1 byte -> battery percentage
  uint8_t  flags;          //1 byte
} Payload; //25 bytes

//this stops the build if the size is not 25 bytes, so the struct cant change by accident without you noticing
//it adds up every field: 2+4+4+1+4+4+4+1+1 = 25
static_assert(sizeof(Payload) == 25, "Payload size changed, the backend parser needs to be updated");
