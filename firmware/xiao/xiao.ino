//#include <Adafruit_TinyUSB.h>
#include <bluefruit.h>
#include <LSM6DS3.h>
#include <Wire.h>

#define SAMPLE_TIME_MS 1000

unsigned long now;
unsigned long lastTime = 0;


typedef struct __attribute__((__packed__)){

  uint32_t sequence; //4 bytes -> number of reading 
  uint32_t uptime_ms; //4 bytes -> timestamp 
  uint8_t heart_bpm; //1 byte -> bpm reading
                     // uint16_t accel_x; //2 bytes -> movement on the x axis
                     // uint16_t accel_y; //2 bytes -> movement on the y axis
                     // uint16_t accel_z; //2 bytes -> movement on the z axis
  uint8_t batt_lvl; //1 byte -> battery percentage 
  uint8_t flags; //1 bye -> 0 = ok, 1 = is test, 2 = error
} Payload; //17 bytes - 6 bytes = 11 bytes

Payload payload;

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


uint8_t readBatteryPercent(){

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


void acquireMeasurement(Payload *mes, uint8_t simulatedHeart){

  mes->sequence++;
  mes->uptime_ms = millis();
  mes->heart_bpm = simulatedHeart;
  mes->batt_lvl = readBatteryPercent();


} 


void setup() {

  //battery reading setup
  pinMode(VBAT_ENABLE, OUTPUT); //enable voltage divider
  digitalWrite(VBAT_ENABLE, LOW);

  analogReference(AR_INTERNAL_3_0); // ADC range: 0–3.0 V
  analogReadResolution(12);        // 4096 ADC steps
  delay(10);


  Serial.begin(115200);

  //Init and start  Bluetooth
  Bluefruit.configPrphBandwidth(BANDWIDTH_MAX);

  Bluefruit.begin();
  Bluefruit.setTxPower(4);
  Bluefruit.setName("XIAO BLE");

  bledis.begin();
  blebas.begin();
  bleuart.begin();

  payload.flags = 1;

  startAdv();

}

void loop() {

  now = millis();

  static uint8_t simulatedHeart = 40;
  if(now - lastTime >= SAMPLE_TIME_MS){

    now = millis();
    lastTime = now;

    if(simulatedHeart >= 180)
      simulatedHeart = 40;

    simulatedHeart++;

    acquireMeasurement(&payload, simulatedHeart);

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
