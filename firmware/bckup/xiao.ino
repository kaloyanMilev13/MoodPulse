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
  uint16_t accel_x; //2 bytes -> movement on the x axis
  uint16_t accel_y; //2 bytes -> movement on the y axis
  uint16_t accel_z; //2 bytes -> movement on the z axis
  uint8_t batt_lvl; //1 byte -> battery percentage 
  uint8_t flags; //1 bye -> 0 = ok, 1 = is test, 2 = error
} Payload; //17 bytes


//Bluetooth
BLEUart bleuart;
BLEDis bledis;
BLEBas blebas;

//Movement

LSM6DS3 myIMU(I2C_MODE, 0x6A);


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


void acquireMeasurement(Payload *mes, uint8_t simulatedHeart){

  mes->sequence++;
  mes->uptime_ms = millis();
  mes->heart_bpm = simulatedHeart;


  //Movement
  mes->accel_x = myIMU.readRawAccelX();
  mes->accel_y = myIMU.readRawAccelY();
  mes->accel_z = myIMU.readRawAccelZ();

} 



Payload payload;

void setup() {
  Serial.begin(115200);

  while (!Serial) {
    delay(10);
  }
  delay(200);

  //Init and start  Bluetooth
  Bluefruit.configPrphBandwidth(BANDWIDTH_MAX);

  Bluefruit.begin();
  Bluefruit.setTxPower(4);
  Bluefruit.setName("XIAO BLE");

  bledis.begin();
  blebas.begin();
  bleuart.begin();

  startAdv();


  //Init IMU
  // The installed Seeed library handles IMU power and Wire1.
  int imuStatus = myIMU.begin();

  Serial.print("IMU begin status: ");
  Serial.println(imuStatus);

  Wire1.beginTransmission(0x6A);
  uint8_t busStatus = Wire1.endTransmission();

  Serial.print("IMU I2C status: ");
  Serial.println(busStatus);

  payload.flags = (imuStatus == 0) ? 0 : 2; 


  for (uint8_t address = 0x6A; address <= 0x6B; address++) {
  Wire1.beginTransmission(address);
  Wire1.write(0x0F);  // WHO_AM_I identification register
  uint8_t status = Wire1.endTransmission();

  Serial.print("Address 0x");
  Serial.print(address, HEX);
  Serial.print(" status: ");
  Serial.println(status);

  if (status == 0) {
    if (Wire1.requestFrom(address, (uint8_t)1) == 1) {
      Serial.print("WHO_AM_I: 0x");
      Serial.println(Wire1.read(), HEX);
    } else {
      Serial.println("Identification read failed");
    }
  }
}

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
    Serial.print("Accel X: ");    Serial.println(payload.accel_x);
    Serial.print("Accel Y: ");    Serial.println(payload.accel_y);
    Serial.print("Accel Z: ");    Serial.println(payload.accel_z);
    Serial.println("-----------------------");


    if(Bluefruit.connected()){

      bleuart.write((uint8_t*)&payload, sizeof(payload)); //cast the addres of stats to a uint8_t pointer
      Serial.println("Data sent over BLE!");

    }


    while(bleuart.available()){

      char ch = (char)bleuart.read();

      if (ch == 't' || ch == 'T') {
        payload.flags = !payload.flags;
        Serial.print("isTest toggled via BLE to: ");
        Serial.println(payload.flags);
      }

    }

  }

}
