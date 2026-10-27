#include <Wire.h>
#include <LiquidCrystal_I2C.h>
#include <WiFi.h>
#include <HTTPClient.h>
#include <ErriezBH1750.h>
#include <ArduinoJson.h>
#include "DHT.h"
#include <HardwareSerial.h>
#include <esp_system.h>

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
#if !defined(WIFI_SSID) || !defined(WIFI_PASSWORD)
#error "secrets.h thieu WIFI_SSID hoac WIFI_PASSWORD"
#endif
// secrets.h cu co the chua co 2 dong nay -> mac dinh: khong ghi Google Sheet, Firebase chua khoa
#ifndef GOOGLE_SCRIPT_URL
#define GOOGLE_SCRIPT_URL ""
#endif
#ifndef FIREBASE_AUTH
#define FIREBASE_AUTH ""
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
// Moi lan goi Firebase (HTTPS) mat ~0.5-1 giay: gui/doc 2 giay/lan de vong lap con thoi gian
// doc cam bien va dieu khien (truoc day 0.7s/1s -> ESP32 gan nhu luon ban mang).
const unsigned long SET_READ_INTERVAL = 2000;
const unsigned long FIREBASE_INTERVAL = 2000;
const unsigned long WIFI_RETRY_MS     = 15000;  // mat WiFi: thu ket noi lai moi 15 giay, KHONG dung ca he thong
unsigned long lastWiFiTry = 0;

// ================= ON DINH RO-LE (che do AUTO) =================
// Vung tre: bat khi vuot nguong, chi tat khi da qua nguong + vung tre -> khong dong ngat lien tuc.
const float HYST_T     = 0.5;   // do C
const float HYST_H     = 2;     // % do am khong khi
const float HYST_SOIL  = 2;     // % do am dat
const float HYST_LIGHT = 50;    // lux
// Moi ro-le doi trang thai toi da 1 lan / 15 giay; 2 thiet bi bat cach nhau >= 0.8 giay
// (dong khoi dong cua bom/quat/phun suong cung luc de gay sut ap, reset ESP32).
const unsigned long MIN_SWITCH_MS = 15000;
const unsigned long INRUSH_GAP_MS = 800;
enum { O_DEN, O_BOM, O_SUONG, O_QUAT, O_MO, O_DONG, O_COUNT };
unsigned long lastSwitchAt[O_COUNT] = {0};
unsigned long lastTurnOnAt = 0;
const unsigned long LCD_INTERVAL      = 1000;

// =====================================================
// WIFI
// =====================================================
// Ket noi WiFi KHONG chan: mat WiFi (modem mat dien, song yeu) thi ESP32 van doc cam bien,
// van dieu khien AUTO theo nguong da luu va hien LCD; chi thu ket noi lai moi WIFI_RETRY_MS.
void startWiFi(unsigned long waitMs) {
  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(true);
  WiFi.begin(ssid, password);
  lastWiFiTry = millis();
  Serial.print("Dang ket noi WiFi");
  while (WiFi.status() != WL_CONNECTED && millis() - lastWiFiTry < waitMs) {
    delay(300);
    Serial.print(".");
  }
  Serial.println();
  if (WiFi.status() == WL_CONNECTED) {
    Serial.print("WiFi da ket noi, IP: ");
    Serial.println(WiFi.localIP());
  } else {
    Serial.println("Chua co WiFi - van chay offline, se thu lai sau");
  }
}

void maintainWiFi() {
  if (WiFi.status() == WL_CONNECTED) return;
  if (millis() - lastWiFiTry < WIFI_RETRY_MS) return;
  lastWiFiTry = millis();
  Serial.println("Mat WiFi - thu ket noi lai");
  WiFi.disconnect();
  WiFi.begin(ssid, password);
}

const char* resetReasonText() {
  switch (esp_reset_reason()) {
    case ESP_RST_POWERON:  return "bat_nguon";
    case ESP_RST_BROWNOUT: return "sut_ap";
    case ESP_RST_PANIC:    return "loi_phan_mem";
    case ESP_RST_INT_WDT:
    case ESP_RST_TASK_WDT:
    case ESP_RST_WDT:      return "treo";
    case ESP_RST_SW:       return "khoi_dong_lai";
    case ESP_RST_EXT:      return "nut_reset";
    default:               return "khac";
  }
}

// =====================================================
// FIREBASE: GET JSON SET + MODE
// =====================================================
bool readSetValues() {
  if (WiFi.status() != WL_CONNECTED) return false;

  HTTPClient http;
  String url = firebaseURL();

  http.begin(url);
  http.setConnectTimeout(3000);
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

  // Lenh bat/tat thiet bi tu web/Zalo chi dung o MANUAL; o AUTO ESP32 tu quyet (giu dung trang thai ro-le)
  if (MODE == 0) {
  if (!doc["DEN"].isNull())   DEN   = doc["DEN"].as<int>();
  if (!doc["BOM"].isNull())   BOM   = doc["BOM"].as<int>();
  if (!doc["SUONG"].isNull()) SUONG = doc["SUONG"].as<int>();
  if (!doc["QUAT"].isNull())  QUAT  = doc["QUAT"].as<int>();
  if (!doc["MO"].isNull())    MO    = doc["MO"].as<int>();
  if (!doc["DONG"].isNull())  DONG  = doc["DONG"].as<int>();
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
  // Chan doan: thoi gian chay (giay), ly do khoi dong lan truoc (vd sut_ap), song WiFi (dBm)
  doc["ESP_UP"]   = millis() / 1000;
  doc["ESP_RST"]  = resetReasonText();
  doc["ESP_RSSI"] = WiFi.RSSI();

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
  http.setConnectTimeout(3000);
  http.setTimeout(2000);

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
  if (googleScriptURL.length() == 0) return false;  // chua cai link Google Sheet
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
  // Trang thai thiet bi -> may chu AI hoc duoc ca luc may tinh tat (google_sheet/Code.gs)
  url += "&bom=" + String(BOM) + "&den=" + String(DEN) + "&quat=" + String(QUAT);
  url += "&suong=" + String(SUONG) + "&mo=" + String(MO) + "&mode=" + String(MODE);

  http.begin(url);
  http.setFollowRedirects(HTTPC_STRICT_FOLLOW_REDIRECTS);
  http.setConnectTimeout(3000);
  http.setTimeout(5000);

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

// Doc analog 9 lan lay trung vi: nguon nhieu/sut ap lam ADC nhay, trung vi loai bo gai nhieu
int analogReadMedian(int pin) {
  const int N = 9;
  int samples[N];
  for (int i = 0; i < N; i++) {
    samples[i] = analogRead(pin);
    delayMicroseconds(300);
  }
  return getMedianNum(samples, N);
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

  int measure = analogReadMedian(PH_PIN);
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
      int rawSoil = analogReadMedian(CBAMDAT);
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
// Doi 1 dau ra o AUTO. force = true (khoa an toan pH/do man): tat ngay, bo qua gioi han.
bool setOutput(int idx, int pin, int &state, int want, bool force) {
  if (want == state) return false;
  unsigned long now = millis();
  if (!force) {
    if (lastSwitchAt[idx] != 0 && now - lastSwitchAt[idx] < MIN_SWITCH_MS) return false;
    if (want == 1 && now - lastTurnOnAt < INRUSH_GAP_MS) return false;
  }
  state = want;
  digitalWrite(pin, state);
  lastSwitchAt[idx] = now;
  if (want == 1) lastTurnOnAt = now;
  return true;
}

void autoMode() {
  autoAudioEnabled = true;

  // ===== DIEU KHIEN THIET BI THEO NGUONG (co vung tre: giua 2 muc thi giu nguyen trang thai) =====
  int wantDEN = DEN;
  if (ANHSANG < SET_AS) wantDEN = 1;
  else if (ANHSANG > SET_AS + HYST_LIGHT) wantDEN = 0;

  int wantMO = MO;
  if (ANHSANG <= SET_AS) wantMO = 1;  // toi -> mo man che
  else if (ANHSANG > SET_AS + HYST_LIGHT) wantMO = 0;

  int wantQUAT = QUAT;
  if (t > SET_T) wantQUAT = 1;
  else if (t < SET_T - HYST_T) wantQUAT = 0;

  int wantSUONG = SUONG;
  if (h < SET_H) wantSUONG = 1;
  else if (h >= min(SET_H + HYST_H, 100.0f)) wantSUONG = 0;

  int wantBOM = BOM;
  if (GT_AMDAT < SET_AMDAT) wantBOM = 1;
  else if (GT_AMDAT >= min(SET_AMDAT + HYST_SOIL, 100.0f)) wantBOM = 0;

  // Khoa an toan: nuoc man -> khong bom; pH ngoai nguong -> khong bom, khong phun suong (tat ngay)
  bool lockBOM   = (GT_DOMAN > SET_DOMAN) || (GT_PH < SET_PHLOW) || (GT_PH > SET_PHHIGH);
  bool lockSUONG = (GT_PH < SET_PHLOW) || (GT_PH > SET_PHHIGH);
  if (lockBOM) wantBOM = 0;
  if (lockSUONG) wantSUONG = 0;

  setOutput(O_BOM, BOM_PIN, BOM, wantBOM, lockBOM);
  setOutput(O_SUONG, SUONG_PIN, SUONG, wantSUONG, lockSUONG);
  setOutput(O_QUAT, QUAT_PIN, QUAT, wantQUAT, false);
  setOutput(O_DEN, DEN_PIN, DEN, wantDEN, false);
  // Man che: MO va DONG luon nguoc nhau, doi cung luc
  setOutput(O_MO, MO_PIN, MO, wantMO, false);
  if (DONG != 1 - MO) {  // ke ca luc vua khoi dong (MO = DONG = 1): khong bao gio cho mo + dong cung luc
    DONG = 1 - MO;
    digitalWrite(DONG_PIN, DONG);
    lastSwitchAt[O_DONG] = millis();
  }

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

  // Cai chan ro-le NGAY khi khoi dong (truoc day doi WiFi + phat nhac 3 giay roi moi cai
  // -> ro-le tha noi, nhay lung tung moi lan ESP32 khoi dong lai do sut dien)
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

  Serial.print("Ly do khoi dong: ");
  Serial.println(resetReasonText());

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

  // WiFi: cho toi da 10 giay, khong co van chay offline (AUTO theo nguong da luu)
  lcd.setCursor(0, 0);
  lcd.print("Dang ket noi WiFi...");
  startWiFi(10000);
  lcd.clear();

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

  maintainWiFi();  // khong chan: mat WiFi van doc cam bien + dieu khien AUTO

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