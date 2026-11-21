#include "ring_buffer.h"

void MP_rb_init(MP_RingBuffer *rb) {
  rb->head = 0;
  rb->tail = 0;
  rb->count = 0;
}

bool MP_rb_isEmpty(const MP_RingBuffer *rb) {
  return rb->count == 0;
}

bool MP_rb_isFull(const MP_RingBuffer *rb) {
  return rb->count == RING_CAPACITY;
}

bool MP_rb_push(MP_RingBuffer *rb, Payload p) {
  if (MP_rb_isFull(rb)) {
    //delete oldest sample to make room
    rb->tail = (rb->tail + 1) % RING_CAPACITY;
    rb->count--;
  }

  rb->data[rb->head] = p;
  rb->head = (rb->head + 1) % RING_CAPACITY;
  rb->count++;
  return true;
}

bool MP_rb_pop(MP_RingBuffer *rb, Payload *out_p) {
  if (MP_rb_isEmpty(rb)) {
    return false;
  }

  *out_p = rb->data[rb->tail];
  rb->tail = (rb->tail + 1) % RING_CAPACITY;
  rb->count--;
  return true;
}

const Payload* MP_rb_peek(const MP_RingBuffer *rb) {
  if (MP_rb_isEmpty(rb)) {
    return nullptr;
  }

  //this gives a pointer to the oldest payload inside the buffer, it is not a copy
  return &rb->data[rb->tail];
}

void MP_rb_drop(MP_RingBuffer *rb) {
  if (MP_rb_isEmpty(rb)) {
    return;
  }

  //same as pop but without copying the payload out, it just forgets the oldest one
  rb->tail = (rb->tail + 1) % RING_CAPACITY;
  rb->count--;
}
