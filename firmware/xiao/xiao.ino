#include <Adafruit_TinyUSB.h>
#include <bluefruit.h>

#define SAMPLE_TIME_MS 1000

unsigned long now;
unsigned long lastTime = 0;

typedef struct{
 
  uint32_t sequence;
  unsigned long uptime_ms; // do i need it instead of "now" var
  uint8_t heart_bpm;
  bool isTest;
 
} Measurements;


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


void acquireMeasurement(Measurements *mes, uint8_t simulatedHeart){
  
    mes->sequence++;
    mes->uptime_ms = millis();
    mes->heart_bpm = simulatedHeart;

} 



Measurements stats = {0, millis(), 0 , 1};

void setup() {
  Serial.begin(115200);

  Bluefruit.configPrphBandwidth(BANDWIDTH_MAX);

  Bluefruit.begin();
  Bluefruit.setTxPower(4);
  Bluefruit.setName("XIAO BLE");

  bledis.begin();
  blebas.begin();
  bleuart.begin();

  startAdv();

}

void loop() {

  now = millis();

  static uint8_t simulatedHeart = 40;
  if(now - lastTime >= SAMPLE_TIME_MS){

    now = millis();
    lastTime = now;

    simulatedHeart++;

    acquireMeasurement(&stats, simulatedHeart);

    Serial.print("Seq: ");
    Serial.println(stats.sequence);

    Serial.print("isTest: ");
    Serial.println(stats.isTest);

    Serial.print("uptime_ms: ");
    Serial.println(stats.uptime_ms);

    Serial.print("Heart rate: ");
    Serial.println(stats.heart_bpm);
  }

}
