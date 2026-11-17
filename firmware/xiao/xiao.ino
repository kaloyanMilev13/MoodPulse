//#include <Adafruit_TinyUSB.h>
#include <bluefruit.h>
#include <LSM6DS3.h>
#include <Wire.h>

#include <MAX30105.h>
#include "heartRate.h" //algorithm library for checkForBeat()

MAX30105 particleSensor;
bool sensorReady = false;

#define SAMPLE_TIME_MS 1000

#define SIMULATE_SENSOR true

#define RING_CAPACITY 64


//==============FLAGS==============
#define FLAG_BLE_ERROR          (1 << 0) // 0x01
#define FLAG_SENSOR_ERROR       (1 << 1) // 0x02
#define FLAG_BUFFER_OVERFLOW    (1 << 2) // 0x04
#define FLAG_LOW_BATTERY        (1 << 3) // 0x08
#define FLAG_POOR_WRIST_CONTACT (1 << 4) // 0x10
#define FLAG_OFFLINE_BUFFERING  (1 << 5) // 0x20
#define FLAG_SIMULATION_MODE    (1 << 6) // 0x40
#define FLAG_BATTERY_CHARGING   (1 << 7) // 0x80


//---------------------------
//im using the xiao nrf52840, not the nrf52840 Sense,
//so it doesnt have the built in mic and built in IMU.
//for the sensor, im using the MAX30102 breakout board
//--------------------------

unsigned long now;
unsigned long lastTime = 0;


typedef struct __attribute__((__packed__)){//this is so that the compiler doesnt add any padding bytes

  uint16_t device_id; //2 bytes -> id of the bracelet (can be 1 byte if not enough space)
  uint32_t sequence; //4 bytes -> number of reading 
  uint32_t uptime_ms; //4 bytes -> timestamp 
  uint8_t heart_bpm; //1 byte -> bpm reading
  uint32_t heart_lastBeat;// 4 bytes -> time of the last heartbeat
  long heart_IrValue; //4 bytes
  long heart_redValue;//4 bytes
  uint8_t batt_lvl; //1 byte -> battery percentage 
  uint8_t flags; //1 byte

} Payload; //25 bytes

Payload payload;


typedef struct {

  float beatsPerMinute;
  unsigned long lastBeat;

  long irValue;
  long redValue;

} HeartMeasurements;

HeartMeasurements heart_mes;


typedef struct {

  Payload data[RING_CAPACITY];

  size_t head;
  size_t tail;
  size_t count;

} MP_RingBuffer;

MP_RingBuffer rb;

//Bluetooth
BLEUart bleuart;
BLEDis bledis;
BLEBas blebas;


void MP_rb_init(MP_RingBuffer *rb){

  rb->head = 0;
  rb->tail = 0;
  rb->count = 0;

}

bool MP_rb_isEmpty(const MP_RingBuffer *rb){

  return rb->count == 0;

}

bool MP_rb_isFull(const MP_RingBuffer *rb){

  return rb->count == RING_CAPACITY;

}

bool MP_rb_push(MP_RingBuffer *rb, Payload p){

  if(MP_rb_isFull(rb))
    return false;

  rb->data[rb->head] = p;
  rb->head = (rb->head + 1) % RING_CAPACITY;
  rb->count++;
  return true;

}


bool MP_rb_pop(MP_RingBuffer *rb, Payload *out_p){

  if(MP_rb_isEmpty(rb))
    return false;

  *out_p = rb->data[rb->tail];
  rb->tail = (rb->tail + 1) % RING_CAPACITY;
  rb->count--;
  return true; 

}




//configure and launch the BLE advertising packets
void startAdv(void){

  //tells scanning devices that this is a classic ble-free(low energy) device and that is is discoverable indefinetely
  Bluefruit.Advertising.addFlags(BLE_GAP_ADV_FLAGS_LE_ONLY_GENERAL_DISC_MODE);

  //includes the transmit power stat in the advertising packet
  Bluefruit.Advertising.addTxPower();

  //advertise the uuid of the ble uart service
  Bluefruit.Advertising.addService(bleuart);



  //add the name to the "scan response" packet, because this way the name is only sent when another device explicitly requests it during a scan. A standard advertisment packet is only 31 bytes, so this practice makes it save space
  Bluefruit.ScanResponse.addName();



  //configures automatic rebroadcasting
  Bluefruit.Advertising.restartOnDisconnect(true);

  //set the timig interval between advertising chirps, fast and slow
  Bluefruit.Advertising.setInterval(32, 244); //uints of 0.625ms

  //this makes the board advertise aggresivelly for the first 30 seconds, and then change to the slow adv interval
  Bluefruit.Advertising.setFastTimeout(30);


  //start advertising, 0 means dont stop until device connets
  Bluefruit.Advertising.start(0);
}


uint8_t readBatteryPercentage(){

  uint32_t total = 0;

  analogRead(PIN_VBAT); //it is suggested to discard the first reading

  for(int i = 0; i < 16; i++){// get 16 readings to average them to reduce noise
    total += analogRead(PIN_VBAT);
  }

  float raw = total / 16.0f; //get the average

  // Undo the XIAO's 1 MΩ / 510 kΩ voltage divider.
  float voltage = raw * (3.0f / 4096.0f) * ((1000.0f + 510.0f) / 510.0f);

  //rough estimate for 3.7v LiPo
  float percent = (voltage - 3.3f) * 100.0f / (4.2f - 3.3f);

  //convert to %
  percent = constrain(percent, 0.0f, 100.0f);

  return (uint8_t)(percent + 0.5f); //why the +0.5f?

}


void getHeart(HeartMeasurements *heart_mes){

  static float beatsPerMinute = 40;

#if SIMULATE_SENSOR
  if(beatsPerMinute >= 180)
    beatsPerMinute = 40;

  beatsPerMinute++;

#else

  if (!sensorReady) return;

  heart_mes->irValue = particleSensor.getIR();
  heart_mes->redValue = particleSensor.getRed();

  if(payload.heart_IrValue < 30000){
    payload.flags |= FLAG_POOR_WRIST_CONTACT;
  }else{
    payload.flags &= ~FLAG_POOR_WRIST_CONTACT;
  }

  if(checkForBeat(heart_mes->irValue) == true){

    unsigned long beatTime = millis();
    unsigned long delta = beatTime - heart_mes->lastBeat;
    if (heart_mes->lastBeat != 0 && delta > 0) {
      beatsPerMinute = 60000.0f / delta;
    }
    heart_mes->lastBeat = beatTime;

  }


#endif

  if(beatsPerMinute <= 255 && beatsPerMinute > 0){

    heart_mes->beatsPerMinute = beatsPerMinute;

  }

}


void acquireMeasurement(Payload *payload, HeartMeasurements *heart_mes){

#if SIMULATE_SENSOR
  getHeart(heart_mes);
#endif

  payload->sequence++;
  payload->uptime_ms = millis();
  payload->heart_bpm = heart_mes->beatsPerMinute;
  payload->heart_IrValue = heart_mes->irValue;
  payload->heart_redValue = heart_mes->redValue;
  payload->heart_lastBeat = heart_mes->lastBeat;
  payload->batt_lvl = readBatteryPercentage();


} 


void setup() {

  //clear flags
  payload.flags = 0;

  //set simulation flag
  if(SIMULATE_SENSOR)
    payload.flags |= FLAG_SIMULATION_MODE;

  // unique id coming from the chip's serial number
  payload.device_id = (uint16_t)(NRF_FICR->DEVICEID[0] >> 16);// >>16 keeps the upper half, because the lower half might be similar/same for the chips and casts as a 16bit because this is the size



  //battery reading setup
  pinMode(VBAT_ENABLE, OUTPUT); //enable voltage divider
  digitalWrite(VBAT_ENABLE, LOW);

  analogReference(AR_INTERNAL_3_0); // ADC range: 0–3.0 V
  analogReadResolution(12);        // 4096 ADC steps
  delay(10);


  //Serial communication
  Serial.begin(115200);

  //Init and start  Bluetooth
  Bluefruit.configPrphBandwidth(BANDWIDTH_MAX);

  if(Bluefruit.begin() == false){
    payload.flags |= FLAG_BLE_ERROR;
  }else {
    Bluefruit.setTxPower(4);
    Bluefruit.setName("XIAO BLE");

    bledis.begin();
    blebas.begin();
    bleuart.begin();

    startAdv();
    payload.flags &= ~FLAG_BLE_ERROR;
  }


#if !SIMULATE_SENSOR
  //MAX30102 Init and Start
  if(particleSensor.begin() == false){
    payload.flags |= FLAG_SENSOR_ERROR;
    sensorReady = true;
  }else {
    particleSensor.setup();
    payload.flags &= ~FLAG_SENSOR_ERROR;
  }
#endif


}


//test var
static int unsent = 0;

void loop() {

#if !SIMULATE_SENSOR
  static bool firstRead = true;
  if (sensorReady && firstRead) {
    Serial.println("Starting first MAX30102 reading...");
    Serial.flush();
  }
  getHeart(&heart_mes);
  if (sensorReady && firstRead) {
    Serial.println("First MAX30102 reading returned.");
    Serial.flush();
    firstRead = false;
  }
#endif

  now = millis();

  if(now - lastTime >= SAMPLE_TIME_MS){

    now = millis();
    lastTime = now;

    //checks if usb-c is connected
    if (NRF_POWER->USBREGSTATUS & POWER_USBREGSTATUS_VBUSDETECT_Msk) {
      payload.flags |= FLAG_BATTERY_CHARGING;
    } else {
      payload.flags &= ~FLAG_BATTERY_CHARGING;
    }

    if(payload.batt_lvl < 20){
      payload.flags |= FLAG_LOW_BATTERY;
    }else{
      payload.flags &= ~FLAG_LOW_BATTERY;
    }

    acquireMeasurement(&payload, &heart_mes);
    unsent++;

    //buffering
    bool buff_success = MP_rb_push(&rb, payload);

    if(!buff_success){
      payload.flags |= FLAG_BUFFER_OVERFLOW;
    }else{
      payload.flags &= ~FLAG_BUFFER_OVERFLOW;
    }

    if(Bluefruit.connected()){
      unsent = 0;
      Payload packetToSend;
      int sentCnt = 0;

      while(!MP_rb_isEmpty(&rb)){
        if(MP_rb_pop(&rb, &packetToSend)){

          packetToSend.flags |= FLAG_OFFLINE_BUFFERING;

          bleuart.write((uint8_t*)&packetToSend, sizeof(packetToSend));
          sentCnt++;
          delay(20);
        }
      }

      packetToSend.flags &= ~FLAG_OFFLINE_BUFFERING;

      Serial.print("Buffer flushed! Sent packets: ");
      Serial.println(sentCnt);
    }

    Serial.print("unsent packages: ");
    Serial.println(unsent);


    // Print to Serial Monitor for local debugging
    Serial.println();
    Serial.println(F("========== MoodPulse payload =========="));

    Serial.print(F("Device ID:       "));
    Serial.print(payload.device_id);
    Serial.print(F(" (0x"));
    Serial.print(payload.device_id, HEX);
    Serial.println(F(")"));

    Serial.print(F("Sequence:        "));
    Serial.println(payload.sequence);

    Serial.print(F("Uptime:          "));
    Serial.print(payload.uptime_ms);
    Serial.println(F(" ms"));

    Serial.print(F("Heart rate:      "));
    Serial.print(payload.heart_bpm);
    Serial.println(F(" BPM"));

    Serial.print(F("Last beat time:  "));
    Serial.print(payload.heart_lastBeat);
    Serial.println(F(" ms since boot"));

    Serial.print(F("IR reading:      "));
    Serial.println(payload.heart_IrValue);

    Serial.print(F("Red reading:     "));
    Serial.println(payload.heart_redValue);

    Serial.print(F("Battery:         "));
    Serial.print(payload.batt_lvl);
    Serial.println(F("%"));


    Serial.print(F("Status Flags (0x"));
    if (payload.flags < 16) Serial.print("0");
    Serial.print(payload.flags, HEX);
    Serial.println(F("):"));

    Serial.print(F("  - BLE Error:          ")); Serial.println((payload.flags & FLAG_BLE_ERROR) ? "YES" : "NO");
    Serial.print(F("  - Sensor Error:       ")); Serial.println((payload.flags & FLAG_SENSOR_ERROR) ? "YES" : "NO");
    Serial.print(F("  - Buffer Overflow:    ")); Serial.println((payload.flags & FLAG_BUFFER_OVERFLOW) ? "YES" : "NO");
    Serial.print(F("  - Low Battery:        ")); Serial.println((payload.flags & FLAG_LOW_BATTERY) ? "YES" : "NO");
    Serial.print(F("  - Poor Wrist Contact: ")); Serial.println((payload.flags & FLAG_POOR_WRIST_CONTACT) ? "YES" : "NO");
    Serial.print(F("  - Offline Buffering:  ")); Serial.println((payload.flags & FLAG_OFFLINE_BUFFERING) ? "YES" : "NO");
    Serial.print(F("  - Simulation Mode:    ")); Serial.println((payload.flags & FLAG_SIMULATION_MODE) ? "YES" : "NO");
    Serial.print(F("  - Battery Charging:   ")); Serial.println((payload.flags & FLAG_BATTERY_CHARGING) ? "YES" : "NO");
    Serial.println(F(")"));

    Serial.print(F("BLE connected:   "));
    Serial.println(Bluefruit.connected() ? F("yes") : F("no"));

    Serial.print(F("Payload size:    "));
    Serial.print(sizeof(payload));
    Serial.println(F(" bytes"));

    Serial.println(F("======================================="));


  }

}
