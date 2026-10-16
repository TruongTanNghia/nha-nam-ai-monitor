#include <Wire.h>
#include <LiquidCrystal_I2C.h>
#include <WiFi.h>
#include <HTTPClient.h>
#include <ErriezBH1750.h>
#include <ArduinoJson.h>
#include "DHT.h"
#include <HardwareSerial.h>

#define softwareSerial         Serial2
#define MCU_SIM_BAUDRATE       9600
#define MCU_SIM_TX_PIN         16
#define MCU_SIM_RX_PIN         17

#include "DFRobotDFPlayerMini.h"
DFRobotDFPlayerMini player;

LiquidCrystal_I2C lcd(0x27, 20, 4);

// WiFi, link Google Sheet, Firebase secret nam trong secrets.h (khong dua len GitHub).
// Lan dau: sao chep secrets.example.h thanh secrets.h roi dien thong tin.
#if __has_include("secrets.h")
#include "secrets.h"
#else
#error "Thieu file secrets.h: sao chep secrets.example.h thanh secrets.h (cung thu muc voi file .ino) roi dien WiFi"
#endif

// ================= WIFI =================
const char* ssid = WIFI_SSID;
const char* password = WIFI_PASSWORD;

// ================= FIREBASE =================
String firebaseHost = "https://nha-kinh-84aa2-default-rtdb.asia-southeast1.firebasedatabase.app";
String googleScriptURL = GOOGLE_SCRIPT_URL;
// Database secret khi da khoa Firebase rules (xem docs/BAO_MAT_FIREBASE.md)
String firebaseAuth = FIREBASE_AUTH;

String firebaseURL() {
  String url = firebaseHost + "/.json";
  if (firebaseAuth.length() > 0) url += "?auth=" + firebaseAuth;
  return url;
}

// ================= CAM BIEN =================
BH1750 sensor(LOW);
float ANHSANG = 0;
float soilRaw = 0;
#define DHTPIN 33
#define DHTTYPE DHT11
DHT dht(DHTPIN, DHTTYPE);
float t = 0;
int h = 0;

#define VREF 3.0
#define SCOUNT 10
int analogBuffer[SCOUNT];
int analogBufferTemp[SCOUNT];
int analogBufferIndex = 0;
int copyIndex = 0;
float averageVoltage = 0;
float GT_DOMAN = 0;

#define CBAMDAT 32
#define CBDOMAN 34
int GT_AMDAT = 0;
//float GT_AMDAT = 0;
int doc_doam = 0;
int tbdoam = 0;

#define PH_PIN 35
float GT_PH = 0;

// ================= NGUONG =================
float SET_AMDAT = 0;
float SET_AS = 0;
float SET_DOMAN = 0;
float SET_H = 0;
float SET_PHHIGH = 0;
float SET_PHLOW = 0;
float SET_T = 0;

// ================= MODE =================
int MODE = 1;   // 0 = manual, 1 = auto

// ================= BAT SOM THEO DU BAO (may chu AI) =================
// May chu AI ghi PRE = {BOM, DEN, QUAT, SUONG, MO, SEQ} khi du bao sap cham nguong.
// AUTO: thiet bi bat khi vuot nguong HOAC co yeu cau bat som; khoa an toan (pH, do man) van uu tien.
// SEQ tang moi lan may chu ghi; SEQ dung yen qua 60 giay (may chu tat/mat mang) -> bo qua yeu cau.
int PRE_BOM = 0, PRE_DEN = 0, PRE_QUAT = 0, PRE_SUONG = 0, PRE_MO = 0;
long preSeq = -1;
unsigned long preSeenAt = 0;
const unsigned long PRE_TIMEOUT = 60000;

// ================= OUTPUT =================
#define DEN_PIN   19
#define BOM_PIN   2
#define SUONG_PIN 4
#define QUAT_PIN  18
#define MO_PIN    13
#define DONG_PIN  27

int DEN = 0;
int BOM = 0;
int SUONG = 0;
int QUAT = 0;
int MO = 1;
int DONG = 1;
int lastMODE = 1;
int lastDEN = 0;
int lastBOM = 0;
int lastSUONG = 0;
int lastQUAT = 0;

// ================= AUDIO =================
unsigned long lastAudioCmd = 0;
const unsigned long AUDIO_GAP = 500;

bool dfPlayerBusy = false;
bool autoAudioEnabled = false;

// Hang doi file am thanh auto
const int AUDIO_QUEUE_SIZE = 20;
int audioQueue[AUDIO_QUEUE_SIZE];
int audioQueueHead = 0;
int audioQueueTail = 0;

// Trang thai dieu kien auto de chi phat 1 lan khi moi vua thoa
bool condDEN   = false;
bool condMO    = false;
bool condQUAT  = false;
bool condSUONG = false;
bool condDOMAN = false;
bool condAMDAT = false;
bool condPH    = false;

// ================= TIMER =================
unsigned long timeSensor   = 0;
unsigned long timeSetRead  = 0;
unsigned long timeFirebase = 0;
unsigned long timeLCD      = 0;
unsigned long timeSheet    = 0;

const unsigned long SHEET_INTERVAL    = 60000;
const unsigned long SENSOR_INTERVAL   = 500;
const unsigned long SET_READ_INTERVAL = 1000;
const unsigned long FIREBASE_INTERVAL = 700;
const unsigned long LCD_INTERVAL      = 1000;

// =====================================================
// WIFI
// =====================================================
void connectWiFi() {
  WiFi.begin(ssid, password);
  Serial.print("Dang ket noi WiFi");
  while (WiFi.status() != WL_CONNECTED) {
    delay(300);
    Serial.print(".");
  }
  Serial.println();
  Serial.println("WiFi da ket noi");
  Serial.print("IP: ");
  Serial.println(WiFi.localIP());
}

// =====================================================
// FIREBASE: GET JSON SET + MODE
// =====================================================
bool readSetValues() {
  if (WiFi.status() != WL_CONNECTED) return false;

  HTTPClient http;
  String url = firebaseURL();

  http.begin(url);
  http.setTimeout(2000);

  int httpCode = http.GET();
  if (httpCode <= 0) {
    Serial.print("GET loi: ");
    Serial.println(httpCode);
    http.end();
    return false;
  }

  String payload = http.getString();
  http.end();

  DynamicJsonDocument doc(2048);
  DeserializationError err = deserializeJson(doc, payload);
  if (err) {
    Serial.println("Loi parse JSON Firebase");
    return false;
  }

  if (!doc["SET_AMDAT"].isNull())  SET_AMDAT  = doc["SET_AMDAT"].as<float>();
  if (!doc["SET_AS"].isNull())     SET_AS     = doc["SET_AS"].as<float>();
  if (!doc["SET_DOMAN"].isNull())  SET_DOMAN  = doc["SET_DOMAN"].as<float>();
  if (!doc["SET_H"].isNull())      SET_H      = doc["SET_H"].as<float>();
  if (!doc["SET_PHHIGH"].isNull()) SET_PHHIGH = doc["SET_PHHIGH"].as<float>();
  if (!doc["SET_PHLOW"].isNull())  SET_PHLOW  = doc["SET_PHLOW"].as<float>();
  if (!doc["SET_T"].isNull())      SET_T      = doc["SET_T"].as<float>();
  if (!doc["MODE"].isNull())       MODE       = doc["MODE"].as<int>();

  if (!doc["DEN"].isNull())   DEN   = doc["DEN"].as<int>();
  if (!doc["BOM"].isNull())   BOM   = doc["BOM"].as<int>();
  if (!doc["SUONG"].isNull()) SUONG = doc["SUONG"].as<int>();
  if (!doc["QUAT"].isNull())  QUAT  = doc["QUAT"].as<int>();
  if (!doc["MO"].isNull())    MO    = doc["MO"].as<int>();
  if (!doc["DONG"].isNull())  DONG  = doc["DONG"].as<int>();

  JsonObject pre = doc["PRE"];
  if (!pre.isNull()) {
    long seq = pre["SEQ"] | -1L;
    if (seq != preSeq) {  // may chu vua ghi yeu cau moi
      preSeq = seq;
      preSeenAt = millis();
    }
    PRE_BOM   = pre["BOM"]   | 0;
    PRE_DEN   = pre["DEN"]   | 0;
    PRE_QUAT  = pre["QUAT"]  | 0;
    PRE_SUONG = pre["SUONG"] | 0;
    PRE_MO    = pre["MO"]    | 0;
  }

  return true;
}

// =====================================================
// FIREBASE: PATCH NHANH
// =====================================================
// Chi ghi gia tri cam bien (+ trang thai thiet bi khi AUTO).
// KHONG ghi SET_* va MODE: web/Zalo la noi doi cac gia tri nay; neu ESP ghi lai
// moi 0.7s se de mat thay doi vua luu tren web truoc khi ESP kip doc.
bool sendToFirebase(bool includeDevices) {
  if (WiFi.status() != WL_CONNECTED) return false;

  HTTPClient http;
  String url = firebaseURL();

  StaticJsonDocument<512> doc;
  doc["ANHSANG"]    = ANHSANG;
  doc["GT_AMDAT"]   = GT_AMDAT;
  doc["GT_DOMAN"]   = GT_DOMAN;
  doc["GT_PH"]      = GT_PH;
  doc["h"]          = h;
  doc["t"]          = t;
  // Nhip tim: Firebase tu dien gio server -> web biet ESP32 con ket noi hay khong
  JsonObject espTs = doc.createNestedObject("ESP_TS");
  espTs[".sv"] = "timestamp";
  // Bao lai SEQ yeu cau bat som da nhan -> web biet firmware ho tro bat som
  doc["PRE_ACK"] = preSeq;

  if (MODE == 1 || includeDevices) {
    doc["BOM"]   = BOM;
    doc["DEN"]   = DEN;
    doc["DONG"]  = DONG;
    doc["MO"]    = MO;
    doc["QUAT"]  = QUAT;
    doc["SUONG"] = SUONG;
  }

  String json;
  serializeJson(doc, json);

  http.begin(url);
  http.addHeader("Content-Type", "application/json");
  http.setTimeout(1500);

  int httpCode = http.PATCH(json);
  http.end();

  if (httpCode <= 0) {
    Serial.print("PATCH loi: ");
    Serial.println(httpCode);
    return false;
  }

  return true;
}

bool sendAllToFirebase() {
  return sendToFirebase(false);
}

// =====================================================
// GOOGLE SHEET
// =====================================================
bool sendToGoogleSheet() {
  if (WiFi.status() != WL_CONNECTED) return false;

  HTTPClient http;

  String url = googleScriptURL;
  url += "?sts=write";
  url += "&t=" + String(t,2);
  url += "&h=" + String(h);
  url += "&amdat=" + String(GT_AMDAT);
  url += "&doman=" + String(GT_DOMAN,0);
  url += "&ph=" + String(GT_PH, 1);
  url += "&anhsang=" + String(ANHSANG,0);

  http.begin(url);
  http.setFollowRedirects(HTTPC_STRICT_FOLLOW_REDIRECTS);
  http.setTimeout(8000);

  int httpCode = http.GET();
  String payload = "";

  if (httpCode > 0) {
    payload = http.getString();
    Serial.print("Google Sheet HTTP Code: ");
    Serial.println(httpCode);
    Serial.print("Google Sheet response: ");
    Serial.println(payload);
    http.end();
    return true;
  } else {
    Serial.print("Google Sheet loi: ");
    Serial.println(httpCode);
    http.end();
    return false;
  }
}

// =====================================================
// MEDIAN
// =====================================================
int getMedianNum(int bArray[], int iFilterLen) {
  int bTab[iFilterLen];
  for (int i = 0; i < iFilterLen; i++) bTab[i] = bArray[i];

  for (int j = 0; j < iFilterLen - 1; j++) {
    for (int i = 0; i < iFilterLen - j - 1; i++) {
      if (bTab[i] > bTab[i + 1]) {
        int temp = bTab[i];
        bTab[i] = bTab[i + 1];
        bTab[i + 1] = temp;
      }
    }
  }

  if (iFilterLen % 2) return bTab[(iFilterLen - 1) / 2];
  return (bTab[iFilterLen / 2] + bTab[iFilterLen / 2 - 1]) / 2;
}

// =====================================================
// DOC CAM BIEN
// =====================================================
void readSensors() {
  ANHSANG = sensor.read();

  float hh = dht.readHumidity();
  float tt = dht.readTemperature();
  if (!isnan(hh) && !isnan(tt)) {
    h = (int)hh;
    t = (float)tt;
  }

  int measure = analogRead(PH_PIN);
  float voltage = 3.3 / 4095.0 * measure;
  GT_PH = 8 + ((2.5 - voltage) / 0.38);

  analogBuffer[analogBufferIndex] = analogRead(CBDOMAN);
  analogBufferIndex = (analogBufferIndex + 1) % SCOUNT;

  for (copyIndex = 0; copyIndex < SCOUNT; copyIndex++) {
    analogBufferTemp[copyIndex] = analogBuffer[copyIndex];
  }

  averageVoltage = getMedianNum(analogBufferTemp, SCOUNT) * VREF / 4095.0;
  GT_DOMAN = (133.42 * averageVoltage * averageVoltage * averageVoltage
            - 255.86 * averageVoltage * averageVoltage
            + 857.39 * averageVoltage) * 0.5;


    if (GT_DOMAN < 100) GT_DOMAN = 0;
    if (GT_DOMAN< 0) GT_DOMAN = 0;

  ////////////////////
      int rawSoil = analogRead(CBAMDAT);
    soilRaw = rawSoil;
    GT_AMDAT = map(rawSoil, 4096, 0, 0, 100);
    GT_AMDAT = GT_AMDAT * 100 / 20;
    GT_AMDAT = constrain(GT_AMDAT, 0, 100);


///////////////////////////

}

// =====================================================
// AUDIO
// =====================================================
bool isQueueEmpty() {
  return audioQueueHead == audioQueueTail;
}

bool isQueueFull() {
  return ((audioQueueTail + 1) % AUDIO_QUEUE_SIZE) == audioQueueHead;
}

void clearAudioQueue() {
  audioQueueHead = 0;
  audioQueueTail = 0;
}

bool enqueueTrack(int track) {
  if (isQueueFull()) {
    Serial.println("Audio queue full!");
    return false;
  }

  audioQueue[audioQueueTail] = track;
  audioQueueTail = (audioQueueTail + 1) % AUDIO_QUEUE_SIZE;

  Serial.print("Enqueue track: ");
  Serial.println(track);
  return true;
}

int dequeueTrack() {
  if (isQueueEmpty()) return -1;

  int track = audioQueue[audioQueueHead];
  audioQueueHead = (audioQueueHead + 1) % AUDIO_QUEUE_SIZE;
  return track;
}

void playTrackOnce(int track) {
  if (millis() - lastAudioCmd < AUDIO_GAP) return;

  player.play(track);
  lastAudioCmd = millis();

  Serial.print("PLAY ONCE: ");
  Serial.println(track);
}

void updateDFPlayerState() {
  while (player.available()) {
    uint8_t type = player.readType();
    int value = player.read();

    if (type == DFPlayerPlayFinished) {
      Serial.print("TRACK FINISHED: ");
      Serial.println(value);
      dfPlayerBusy = false;
    }

    if (type == DFPlayerError) {
      Serial.print("DFPlayer error: ");
      Serial.println(value);
      dfPlayerBusy = false;
    }
  }
}

void processAutoAudioQueue() {
  if (!autoAudioEnabled) return;
  if (dfPlayerBusy) return;
  if (isQueueEmpty()) return;
  if (millis() - lastAudioCmd < AUDIO_GAP) return;

  int track = dequeueTrack();
  if (track != -1) {
    player.play(track);
    dfPlayerBusy = true;
    lastAudioCmd = millis();

    Serial.print("AUTO PLAY QUEUE: ");
    Serial.println(track);
  }
}

void resetAutoConditionFlags() {
  condDEN   = false;
  condMO    = false;
  condQUAT  = false;
  condSUONG = false;
  condDOMAN = false;
  condAMDAT = false;
  condPH    = false;
}

void stopAutoAudioSystem() {
  player.stop();
  dfPlayerBusy = false;
  autoAudioEnabled = false;
  clearAudioQueue();
  resetAutoConditionFlags();
  lastAudioCmd = millis();
  Serial.println("STOP AUTO AUDIO SYSTEM");
}

// =====================================================
// MANUAL MODE
// =====================================================
void manualMode() {
  autoAudioEnabled = false;
  clearAudioQueue();

  // ===== DEN =====
  int realDEN = digitalRead(DEN_PIN);

  if (realDEN != lastDEN) {
    DEN = realDEN;

    if (DEN == 1) playTrackOnce(9);
    else          playTrackOnce(2);

    lastDEN = realDEN;
  } else if (DEN != realDEN) {
    digitalWrite(DEN_PIN, DEN);

    if (DEN == 1) playTrackOnce(9);
    else          playTrackOnce(2);

    lastDEN = DEN;
  }

  // ===== BOM =====
  int realBOM = digitalRead(BOM_PIN);

  if (realBOM != lastBOM) {
    BOM = realBOM;
    lastBOM = realBOM;
  } else if (BOM != realBOM) {
    digitalWrite(BOM_PIN, BOM);
    lastBOM = BOM;
  }

  // ===== SUONG =====
  int realSUONG = digitalRead(SUONG_PIN);

  if (realSUONG != lastSUONG) {
    SUONG = realSUONG;

    if (SUONG == 1) playTrackOnce(11);
    else            playTrackOnce(16);

    lastSUONG = realSUONG;
  } else if (SUONG != realSUONG) {
    digitalWrite(SUONG_PIN, SUONG);

    if (SUONG == 1) playTrackOnce(11);
    else            playTrackOnce(16);

    lastSUONG = SUONG;
  }

  // ===== QUAT =====
  int realQUAT = digitalRead(QUAT_PIN);

  if (realQUAT != lastQUAT) {
    QUAT = realQUAT;

    if (QUAT == 1) playTrackOnce(13);
    else           playTrackOnce(15);

    lastQUAT = realQUAT;
  } else if (QUAT != realQUAT) {
    digitalWrite(QUAT_PIN, QUAT);

    if (QUAT == 1) playTrackOnce(13);
    else           playTrackOnce(15);

    lastQUAT = QUAT;
  }

  // ===== CHONG XUNG DOT MO/DONG =====
  if (MO == 1) DONG = 0;
  if (DONG == 1) MO = 0;

  // ===== MO =====
  int realMO = digitalRead(MO_PIN);
  if (MO != realMO) {
    digitalWrite(MO_PIN, MO);
  }

  // ===== DONG =====
  int realDONG = digitalRead(DONG_PIN);
  if (DONG != realDONG) {
    digitalWrite(DONG_PIN, DONG);
  }
}

// =====================================================
// AUTO MODE
// =====================================================
void autoMode() {
  autoAudioEnabled = true;

  // Yeu cau bat som con hieu luc khi may chu van cap nhat trong 60 giay gan nhat
  bool pre = preSeq >= 0 && (millis() - preSeenAt) < PRE_TIMEOUT;

  // ===== DIEU KHIEN THIET BI =====
  if (ANHSANG < SET_AS || (pre && PRE_DEN)) DEN = 1;
  else DEN = 0;

  if (ANHSANG > SET_AS && !(pre && PRE_MO)) {  // sap toi -> mo man che som
    MO = 0;
    DONG = 1;
  } else {
    MO = 1;
    DONG = 0;
  }

  if (t > SET_T || (pre && PRE_QUAT)) QUAT = 1;
  else QUAT = 0;

  if (h < SET_H || (pre && PRE_SUONG)) SUONG = 1;
  else SUONG = 0;

  // Yeu cau BOM
  if (GT_DOMAN > SET_DOMAN) {
    BOM = 0;
  } else if (GT_AMDAT < SET_AMDAT || (pre && PRE_BOM)) {
    BOM = 1;
  } else {
    BOM = 0;
  }

  // pH ngoai nguong thi ep BOM = 0 va SUONG = 0
  if ((GT_PH < SET_PHLOW) || (GT_PH > SET_PHHIGH)) {
    BOM = 0;
    SUONG = 0;
  }

  digitalWrite(DEN_PIN, DEN);
  digitalWrite(BOM_PIN, BOM);
  digitalWrite(SUONG_PIN, SUONG);
  digitalWrite(QUAT_PIN, QUAT);
  digitalWrite(MO_PIN, MO);
  digitalWrite(DONG_PIN, DONG);

  // ===== THEM AM THANH VAO HANG DOI - CHI 1 LAN KHI MOI THOA =====
  bool nowDEN   = (DEN == 1);
  bool nowMO    = (MO == 0);
  bool nowQUAT  = (QUAT == 1);
  bool nowSUONG = (SUONG == 1);
  bool nowDOMAN = (GT_DOMAN > SET_DOMAN);
  bool nowAMDAT = (GT_AMDAT < SET_AMDAT);
  bool nowPH    = ((GT_PH < SET_PHLOW) || (GT_PH > SET_PHHIGH));

  // DEN = 1 -> play(3)
  if (nowDEN && !condDEN) {
    enqueueTrack(3);
  }
  condDEN = nowDEN;

  // MO = 0 -> play(4)
  if (nowMO && !condMO) {
    enqueueTrack(4);
  }
  condMO = nowMO;

  // QUAT = 1 -> play(7)
  if (nowQUAT && !condQUAT) {
    enqueueTrack(7);
  }
  condQUAT = nowQUAT;

  // SUONG = 1 -> play(5)
  if (nowSUONG && !condSUONG) {
    enqueueTrack(5);
  }
  condSUONG = nowSUONG;

  // GT_DOMAN > SET_DOMAN -> play(12), BOM = 0
  if (nowDOMAN && !condDOMAN) {
    enqueueTrack(12);
  }
  condDOMAN = nowDOMAN;

  // GT_AMDAT < SET_AMDAT -> play(6), BOM = 1
  if (nowAMDAT && !condAMDAT) {
    enqueueTrack(6);
  }
  condAMDAT = nowAMDAT;

  // dieu kien dong code 375 -> play(1)
  if (nowPH && !condPH) {
    enqueueTrack(1);
  }
  condPH = nowPH;
}

// =====================================================
// CONTROL DEVICES
// =====================================================
void controlDevices() {
  if (lastMODE == 1 && MODE == 0) {
    DEN = 0;
    BOM = 0;
    SUONG = 0;
    QUAT = 0;

    digitalWrite(DEN_PIN, DEN);
    digitalWrite(BOM_PIN, BOM);
    digitalWrite(SUONG_PIN, SUONG);
    digitalWrite(QUAT_PIN, QUAT);
    digitalWrite(MO_PIN, MO);
    digitalWrite(DONG_PIN, DONG);

    stopAutoAudioSystem();
    // Ghi ca trang thai thiet bi (da tat) len Firebase, neu khong lan doc ke tiep
    // se lay lai trang thai cu cua che do AUTO va bat lai thiet bi
    sendToFirebase(true);
  }

  if (lastMODE == 0 && MODE == 1) {
    clearAudioQueue();
    dfPlayerBusy = false;
    resetAutoConditionFlags();
  }

  if (MODE == 0) {
    manualMode();
  } else {
    autoMode();
  }

  lastMODE = MODE;
}

// =====================================================
// LCD
// =====================================================
void updateLCD() {
  lcd.setCursor(0, 0);
  lcd.print("T:"); lcd.print(t, 2);
  lcd.print((char)223); lcd.print("C  ");

  lcd.setCursor(8, 0);
  lcd.print("H:"); lcd.print(h);
  lcd.print("% ");

  lcd.setCursor(14, 0);
  lcd.print("MODE:"); lcd.print(MODE, 0);

  lcd.setCursor(0, 1);
  lcd.print("AM DAT:"); lcd.print(GT_AMDAT, 0);
  lcd.print("% ");

  lcd.setCursor(0, 2);
  lcd.print("DO MAN:"); lcd.print(GT_DOMAN, 0);
  lcd.print(" ppm");

  lcd.setCursor(0, 3);
  lcd.print("pH:"); lcd.print(GT_PH, 2);

  lcd.setCursor(8, 3);
  lcd.print("AS:"); lcd.print(ANHSANG, 0);
  lcd.print("Lux  ");
}

// =====================================================
// SETUP
// =====================================================
void setup() {
  Serial.begin(115200);

  connectWiFi();

  Wire.begin();
  sensor.begin(ModeContinuous, ResolutionHigh);
  sensor.startConversion();
  dht.begin();
  lcd.begin();
  //lcd.init();
  lcd.backlight();

  // ===== DFPLAYER =====
  softwareSerial.begin(MCU_SIM_BAUDRATE, SERIAL_8N1, MCU_SIM_RX_PIN, MCU_SIM_TX_PIN);

  if (player.begin(softwareSerial)) {
    Serial.println("Dfplayer OK");
    player.volume(27);
    player.play(17);
    delay(3000);
    player.stop();

    dfPlayerBusy = false;
    autoAudioEnabled = false;
    clearAudioQueue();
    resetAutoConditionFlags();
  } else {
    Serial.println("Connecting to DFPlayer Mini failed!");
  }

  pinMode(CBAMDAT, INPUT);
  pinMode(CBDOMAN, INPUT);
  pinMode(PH_PIN, INPUT);

  pinMode(DEN_PIN, OUTPUT);
  pinMode(BOM_PIN, OUTPUT);
  pinMode(SUONG_PIN, OUTPUT);
  pinMode(QUAT_PIN, OUTPUT);
  pinMode(MO_PIN, OUTPUT);
  pinMode(DONG_PIN, OUTPUT);

  digitalWrite(DEN_PIN, LOW);
  digitalWrite(BOM_PIN, LOW);
  digitalWrite(SUONG_PIN, LOW);
  digitalWrite(QUAT_PIN, LOW);
  digitalWrite(MO_PIN, HIGH);
  digitalWrite(DONG_PIN, HIGH);

  lastDEN   = digitalRead(DEN_PIN);
  lastBOM   = digitalRead(BOM_PIN);
  lastSUONG = digitalRead(SUONG_PIN);
  lastQUAT  = digitalRead(QUAT_PIN);

  readSetValues();
  readSensors();
  controlDevices();
  sendAllToFirebase();
  updateLCD();
}

// =====================================================
// LOOP
// =====================================================
void loop() {
  updateDFPlayerState();
  processAutoAudioQueue();

  if (WiFi.status() != WL_CONNECTED) {
    connectWiFi();
  }

  unsigned long now = millis();

  if (now - timeSensor >= SENSOR_INTERVAL) {
    timeSensor = now;
    readSensors();
    controlDevices();
  }

  if (now - timeSetRead >= SET_READ_INTERVAL) {
    timeSetRead = now;
    readSetValues();
  }

  if (now - timeFirebase >= FIREBASE_INTERVAL) {
    timeFirebase = now;
    sendAllToFirebase();
  }

  if (now - timeLCD >= LCD_INTERVAL) {
    timeLCD = now;
    updateLCD();
  }

  if (now - timeSheet >= SHEET_INTERVAL) {
    timeSheet = now;
    sendToGoogleSheet();
  }
}