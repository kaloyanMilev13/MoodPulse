#pragma once

#include <stdint.h>
#include <stddef.h>
#include "payload.h"

//prints one payload in a readable way on the serial monitor
//it only reads, it never changes anything, so you can delete the call in loop() and the project still works
//p is the copy that goes in the buffer, so the flags printed here are the same ones that are sent (including offline buffering)
void printPayload(const Payload *p, bool bleConnected, size_t bufferedPackets, uint32_t sentPackets);
