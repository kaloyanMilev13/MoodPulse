#include <Arduino.h>
#include <NimBLEDevice.h>

#include "ble_service.h"

// BLE UUIDs (Nordic UART + Battery Service)
//a uuid is the id the phone uses to find a service or a characteristic in the gatt table of the bracelet
//the nordic uart ones are the same ones BLEUart used on the nrf52, so the phone app and the backend dont need to change
//0001 is the service, 0002 is RX (the phone writes, the bracelet receives) and 0003 is TX (the bracelet notifies, the phone receives)
#define NUS_SERVICE_UUID        "6E400001-B5A3-F393-E0A9-E50E24DCCA9E"
#define NUS_RX_CHAR_UUID        "6E400002-B5A3-F393-E0A9-E50E24DCCA9E"
#define NUS_TX_CHAR_UUID        "6E400003-B5A3-F393-E0A9-E50E24DCCA9E"
//180F and 2A19 are the standard battery service and battery level characteristic, the bluetooth sig made them so phones already know them
//the level is 1 byte from 0 to 100, which is exactly what batt_lvl is
#define BAS_SERVICE_UUID        "180F"
#define BAS_LEVEL_CHAR_UUID     "2A19"

//Bluetooth
// NimBLE handles & state
static NimBLEServer* pServer = nullptr;
static NimBLECharacteristic* pTxChar = nullptr;
static NimBLECharacteristic* pBattChar = nullptr;

//two flags are needed because being connected is not the same as the phone having enabled notifications
static volatile bool bleConnected = false;
static volatile bool txNotifyEnabled = false;

//the 3 classes below are callbacks, the stack calls these functions when something happens (a phone connects, subscribes, writes...)
//they run in the bluetooth task and not in loop(), thats why the flags are volatile and the callbacks are kept short
class ServerCallbacks : public NimBLEServerCallbacks {

  void onConnect(NimBLEServer* server, NimBLEConnInfo& connInfo) override {
    (void)server;
    (void)connInfo;
    bleConnected = true;
    Serial.println("BLE client connected.");
  }

  void onDisconnect(NimBLEServer* server, NimBLEConnInfo& connInfo, int reason) override {
    (void)server;
    (void)connInfo;
    (void)reason;
    bleConnected = false;
    txNotifyEnabled = false;
    Serial.println("BLE client disconnected. Restarting advertising...");
    //configures automatic rebroadcasting
    NimBLEDevice::startAdvertising();
  }
};

class TxCharCallbacks : public NimBLECharacteristicCallbacks {
  //this is called when the phone turns notifications on or off for the TX characteristic (subValue is 0 when off)
  void onSubscribe(NimBLECharacteristic* pCharacteristic, NimBLEConnInfo& connInfo, uint16_t subValue) override {
    (void)pCharacteristic;
    (void)connInfo;
    txNotifyEnabled = (subValue != 0);
  }
};

class RxCharCallbacks : public NimBLECharacteristicCallbacks {

  //this is called when the phone writes something to the RX characteristic
  void onWrite(NimBLECharacteristic* pCharacteristic, NimBLEConnInfo& connInfo) override {
    (void)connInfo;
    std::string rxData = pCharacteristic->getValue();
    if (!rxData.empty()) {
      Serial.print("BLE RX: ");
      Serial.println(rxData.c_str());
    }
  }

};

bool initNimBLE(const char* deviceName) {
  NimBLEDevice::init(deviceName);
  NimBLEDevice::setPower(ESP_PWR_LVL_P9); // sent transmit power - up to +9 dBm on ESP32-C3

  pServer = NimBLEDevice::createServer();
  if (!pServer) {
    return false;
  }

  //new is used because the stack keeps this pointer forever, a normal variable would be gone when this function ends
  pServer->setCallbacks(new ServerCallbacks());

  // Battery Service (0x180F)
  NimBLEService* pBattService = pServer->createService(BAS_SERVICE_UUID);
  if (!pBattService) {
    return false;
  }
  pBattChar = pBattService->createCharacteristic(
    BAS_LEVEL_CHAR_UUID,
    NIMBLE_PROPERTY::READ | NIMBLE_PROPERTY::NOTIFY
  );
  uint8_t initialBatt = 100;
  pBattChar->setValue(&initialBatt, 1);
  pBattService->start();

  // Nordic UART Service (NUS)
  NimBLEService* pUartService = pServer->createService(NUS_SERVICE_UUID);
  if (!pUartService) {
    return false;
  }

  pTxChar = pUartService->createCharacteristic(
    NUS_TX_CHAR_UUID,
    NIMBLE_PROPERTY::NOTIFY
  );
  pTxChar->setCallbacks(new TxCharCallbacks());

  NimBLECharacteristic* pRxChar = pUartService->createCharacteristic(
    NUS_RX_CHAR_UUID,
    NIMBLE_PROPERTY::WRITE | NIMBLE_PROPERTY::WRITE_NR
  );
  pRxChar->setCallbacks(new RxCharCallbacks());

  pUartService->start();

  NimBLEAdvertising* pAdvertising = NimBLEDevice::getAdvertising();
  //advertise the uuid of the ble uart service
  pAdvertising->addServiceUUID(NUS_SERVICE_UUID);
  pAdvertising->addServiceUUID(BAS_SERVICE_UUID);
  pAdvertising->enableScanResponse(true);
  //set the timig interval between advertising chirps, fast and slow
  pAdvertising->setMinInterval(32);  // 20 ms (32 * 0.625 ms)
  pAdvertising->setMaxInterval(244); // 152.5 ms (244 * 0.625 ms)
  //start advertising, 0 means dont stop until device connets
  pAdvertising->start();

  return true;
}

bool bleIsConnected() {
  return bleConnected;
}

bool bleCanSend() {
  return bleConnected && txNotifyEnabled && pTxChar != nullptr;
}

bool bleSend(const uint8_t *data, size_t len) {
  if (pTxChar == nullptr) {
    return false;
  }

  pTxChar->setValue(data, len);

  //notify() returns true if the stack accepted the data, it does not mean the phone already got it
  return pTxChar->notify();
}

void bleSendBattery(uint8_t percent) {
  if (bleConnected && pBattChar != nullptr) {
    pBattChar->setValue(&percent, 1);
    pBattChar->notify();
  }
}
