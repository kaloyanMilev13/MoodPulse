#pragma once

#include <stddef.h>
#include "config.h"
#include "payload.h"

//a ring buffer is an array that wraps around when it reaches the end
//it holds the payloads until they are sent, so nothing is lost while the phone is not connected
//head = where the next payload is written, tail = the oldest payload that was not sent yet, count = how many are stored
//the struct is defined here (and not hidden) because the main file needs to own one

typedef struct {
  Payload data[RING_CAPACITY];
  size_t head;
  size_t tail;
  size_t count;
} MP_RingBuffer;

void MP_rb_init(MP_RingBuffer *rb);
bool MP_rb_isEmpty(const MP_RingBuffer *rb);
bool MP_rb_isFull(const MP_RingBuffer *rb);
bool MP_rb_push(MP_RingBuffer *rb, Payload p);
bool MP_rb_pop(MP_RingBuffer *rb, Payload *out_p);

//peek and drop are pop split in 2 steps- look at the oldest payload, and only remove it later
//this is needed because a packet must stay in the buffer until the bluetooth stack accepted it
const Payload* MP_rb_peek(const MP_RingBuffer *rb); //returns nullptr if the buffer is empty
void MP_rb_drop(MP_RingBuffer *rb);
