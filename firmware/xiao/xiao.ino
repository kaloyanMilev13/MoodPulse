//#include <Adafruit_TinyUSB.h>
#include <bluefruit.h>
#include <LSM6DS3.h>
#include <Wire.h>

#include <MAX30105.h>
#include "heartRate.h" //algorithm library for checkForBeat()

MAX30105 particleSensor;

#define SAMPLE_TIME_MS 1000

#define SIMULATE_SENSOR true



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
  uint8_t isTest; //1 byte -> test flag, simulated readings
  uint8_t flags; //1 bye -> 0 = ok, 1 = BlE Error, 2 =MAX Sensor error, 3 = other error
} Payload; //26 bytes

Payload payload;


typedef struct {

  float beatsPerMinute;
  unsigned long lastBeat;

  long irValue;
  long redValue;

} HeartMeasurements;

HeartMeasurements heart_mes;

//Bluetooth
BLEUart bleuart;
BLEDis bledis;
BLEBas blebas;


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
  float voltage = raw * (3.0f / 4096.0f) * ((1000.0f / 510.0f) / 510.0f);

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

  heart_mes->irValue = particleSensor.getIR();
  heart_mes->redValue = particleSensor.getRed();

  if(checkForBeat(heart_mes->irValue) == true){

    long delta = millis() - heart_mes->lastBeat;
    heart_mes->lastBeat = millis();

    beatsPerMinute = 60 / (delta / 1000.0);
  
  }


#endif

    if(beatsPerMinute <= 255 && beatsPerMinute > 20){

      heart_mes->beatsPerMinute = beatsPerMinute;

    }

}


void acquireMeasurement(Payload *payload, HeartMeasurements *heart_mes){

  getHeart(heart_mes);

  payload->sequence++;
  payload->uptime_ms = millis();
  payload->heart_bpm = heart_mes->beatsPerMinute;
  payload->heart_IrValue = heart_mes->irValue;
  payload->heart_redValue = heart_mes->redValue;
  payload->heart_lastBeat = heart_mes->lastBeat;
  payload->batt_lvl = readBatteryPercentage();


} 


void setup() {

  //TEST MODE
  payload.isTest = 1;


  // unique id coming from the chip's serial number
  payload.device_id = (uint16_t)(NRF_FICR->DEVICEID[0] >> 16);// >>16 keeps the upper half, because the lower half might be similar/same for the chips and casts as a 16bit because this is the size

  //start with no errors
  payload.flags = 0;



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
    payload.flags = 1;
  }else {
    Bluefruit.setTxPower(4);
    Bluefruit.setName("XIAO BLE");

    bledis.begin();
    blebas.begin();
    bleuart.begin();

    startAdv();

  }



  //MAX30102 Init and Start
  if(particleSensor.begin() == false){
    payload.flags = 2;
  }else {
    particleSensor.setup();
  }


}

void loop() {

  now = millis();

  if(now - lastTime >= SAMPLE_TIME_MS){

    now = millis();
    lastTime = now;

    acquireMeasurement(&payload, &heart_mes);

    // Print to Serial Monitor for local debugging
    Serial.print("Seq: ");        Serial.println(payload.sequence);
    Serial.print("Flags: ");      Serial.println(payload.flags);
    Serial.print("uptime_ms: ");  Serial.println(payload.uptime_ms);
    Serial.print("Heart rate: "); Serial.println(payload.heart_bpm);
    Serial.print("Battery: "); Serial.print(payload.batt_lvl); Serial.println("%");
    Serial.println("-----------------------");


    if(Bluefruit.connected()){

      bleuart.write((uint8_t*)&payload, sizeof(payload)); //cast the addres of stats to a uint8_t pointer
      Serial.println("Data sent over BLE!");

    }


    while(bleuart.available()){

      char ch = (char)bleuart.read();

      /*  if (ch == 't' || ch == 'T') {
          payload.flags = !payload.flags;
          Serial.print("isTest toggled via BLE to: ");
          Serial.println(payload.flags);
          }*/

    }

  }

}
