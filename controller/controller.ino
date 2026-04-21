#include <Arduino.h>
#include <Wire.h>
#include <Adafruit_PN532.h>

// ================= NFC =================
Adafruit_PN532 nfc(-1);

// ================= CONFIG =================
#define LOCKERS 3

// LOCKS
int lockPins[LOCKERS] = {22, 23, 24};

// REED SWITCHES
int reedPins[LOCKERS] = {30, 31, 32};

// SANITIZATION (UV / MIST / FAN)
int uvPins[LOCKERS]   = {40, 41, 42};
int mistPins[LOCKERS] = {43, 44, 45};
int fanPins[LOCKERS]  = {46, 47, 48};

// COIN
#define COIN_PIN 2
volatile int coinPulse = 0;

// ================= SETUP =================
void setup() {
  Serial.begin(115200);
  Wire.begin();

  nfc.begin();
  nfc.SAMConfig();

  for (int i = 0; i < LOCKERS; i++) {
    pinMode(lockPins[i], OUTPUT);
    pinMode(reedPins[i], INPUT_PULLUP);

    pinMode(uvPins[i], OUTPUT);
    pinMode(mistPins[i], OUTPUT);
    pinMode(fanPins[i], OUTPUT);

    digitalWrite(lockPins[i], HIGH);
    digitalWrite(uvPins[i], LOW);
    digitalWrite(mistPins[i], LOW);
    digitalWrite(fanPins[i], LOW);
  }

  pinMode(COIN_PIN, INPUT_PULLUP);
  attachInterrupt(digitalPinToInterrupt(COIN_PIN), coinISR, FALLING);

  Serial.println("[READY]");
}

// ================= LOOP =================
String cmd = "";

void loop() {
  while (Serial.available()) {
    char c = Serial.read();

    if (c == '\n') {
      handle(cmd);
      cmd = "";
    } else {
      cmd += c;
    }
  }
}

// ================= HANDLER =================
void handle(String c) {
  c.trim();

  // NFC READ
  if (c == "nfcread") {
    readNFC();
  }

  // STORE
  else if (c.startsWith("store:")) {
    int locker = getVal(c, ':', 1);
    unlock(locker);
    delay(2000);
    lock(locker);

    Serial.println("STORE-DONE-" + String(locker));
    sanitise(locker);
  }

  // CLAIM
  else if (c.startsWith("claim:")) {
    int locker = getVal(c, ':', 1);
    unlock(locker);
    delay(2000);
    lock(locker);

    Serial.println("CLAIM-DONE-" + String(locker));
  }

  // SANITISE
  else if (c.startsWith("sanitise:")) {
    int locker = getVal(c, ':', 1);
    sanitise(locker);
  }

  // DOOR
  else if (c.startsWith("doorlock:")) {
    int locker = getVal(c, ':', 1);
    lock(locker);
    Serial.println("DOORLOCK-" + String(locker));
  }

  else if (c.startsWith("doorunlock:")) {
    int locker = getVal(c, ':', 1);
    unlock(locker);
    Serial.println("DOORUNLOCK-" + String(locker));
  }

  // COIN PAYMENT
  else if (c.startsWith("coinpayment:")) {
    int cost = getVal(c, ':', 1);
    coinPayment(cost);
  }
}

// ================= NFC =================
void readNFC() {
  uint8_t uid[7];
  uint8_t uidLength;

  if (nfc.readPassiveTargetID(PN532_MIFARE_ISO14443A, uid, &uidLength)) {

    Serial.print("NFCREAD-");

    for (uint8_t i = 0; i < uidLength; i++) {
      Serial.print(uid[i], HEX);
    }

    Serial.println();
  } else {
    Serial.println("NFCREAD-FAIL");
  }
}

// ================= SANITISE =================
void sanitise(int locker) {

  // UV
  digitalWrite(uvPins[locker], HIGH);
  delay(3000);
  digitalWrite(uvPins[locker], LOW);

  // MIST
  digitalWrite(mistPins[locker], HIGH);
  delay(2000);
  digitalWrite(mistPins[locker], LOW);

  // FAN
  digitalWrite(fanPins[locker], HIGH);
  delay(3000);
  digitalWrite(fanPins[locker], LOW);

  Serial.println("SANITISE-DONE-" + String(locker));
}

// ================= COIN =================
void coinISR() {
  coinPulse++;
}

void coinPayment(int cost) {
  coinPulse = 0;
  int total = 0;

  unsigned long start = millis();

  while (millis() - start < 120000) {

    if (coinPulse > 0) {
      total += coinPulse;
      coinPulse = 0;

      Serial.println("TOTAL-" + String(total));
    }

    if (total >= cost) {
      Serial.println("COINPAYMENT-SUCCESS");
      return;
    }
  }
}

// ================= LOCK =================
void lock(int i) {
  digitalWrite(lockPins[i], HIGH);
}

void unlock(int i) {
  digitalWrite(lockPins[i], LOW);
}

// ================= PARSER =================
int getVal(String data, char sep, int index) {
  int found = 0;
  int start = 0;
  int end = -1;

  for (int i = 0; i <= data.length(); i++) {
    if (i == data.length() || data.charAt(i) == sep) {
      found++;
      if (found == index + 1) {
        start = end + 1;
        end = i;
        break;
      }
      end = i;
    }
  }

  return data.substring(start, end).toInt();
}