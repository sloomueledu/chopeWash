#include <WiFi.h>
#include <PubSubClient.h>
#include <WiFiClientSecure.h>
#include <BLEDevice.h>
#include <BLEUtils.h>
#include <BLEClient.h>
#include <time.h>

#include "secrets.h"

#if USE_ENTERPRISE_WIFI
#include "esp_wpa2.h"
#endif

#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>

#define SCREEN_WIDTH 128
#define SCREEN_HEIGHT 64
#define OLED_RESET    -1
#define SCREEN_ADDRESS 0x3C  // Default I2C address for 0.96" OLED (try 0x3D if blank)

Adafruit_SSD1306 display(SCREEN_WIDTH, SCREEN_HEIGHT, &Wire, OLED_RESET);
// Global status strings
String oledStatus = "Booting...";
String oledCommand = "None";
String oledMachine = "Idle";

void updateOLED(String status, String machine, String cmd) {
  oledStatus = status;
  oledMachine = machine;
  oledCommand = cmd;

  display.clearDisplay();
  display.setTextColor(SSD1306_WHITE);

  // Header Title
  display.setTextSize(1);
  display.setCursor(0, 0);
  display.print("--- chopeWash Bridge ---");
  display.drawLine(0, 10, 127, 10, SSD1306_WHITE);

  // Wi-Fi / Broker Status
  display.setCursor(0, 14);
  display.print("Net: ");
  display.println(oledStatus);

  // Machine Actuated
  display.setCursor(0, 28);
  display.print("Unit: ");
  display.println(oledMachine);

  // Last Command / Feedback
  display.setCursor(0, 42);
  display.print("Cmd: ");
  display.println(oledCommand);

  // IP Address footprint at bottom
  display.setCursor(0, 56);
  if (WiFi.status() == WL_CONNECTED) {
    display.print("IP: ");
    display.println(WiFi.localIP().toString());
  } else {
    display.println("IP: Disconnected");
  }

  display.display();
}

// ==========================================
// ADAFRUIT IO & MQTT CONFIGURATION
// ==========================================
const char* mqtt_server    = "io.adafruit.com";
const int   mqtt_port      = 8883;
const char* IO_USERNAME    = SECRET_IO_USER;
const char* IO_KEY         = SECRET_IO_KEY;
const char* feedback_topic = SECRET_IO_USER "/feeds/chopewash-feedback";

// ==========================================
// CRYPTOGRAPHIC TRUST ANCHOR (DigiCert Global Root G2)
// ==========================================
const char* adafruit_ca_cert = \
"-----BEGIN CERTIFICATE-----\n" \
"MIIDjjCCAnagAwIBAgIQAzrx5qcRqaC7KGSxHQn65TANBgkqhkiG9w0BAQsFADBhMQswCQYDVQQG\n" \
"EwJVUzEVMBMGA1UEChMMRGlnaUNlcnQgSW5jMRkwFwYDVQQLExB3d3cuZGlnaWNlcnQuY29tMSAw\n" \
"HgYDVQQDExdEaWdpQ2VydCBHbG9iYWwgUm9vdCBHMjAeFw0xMzA4MDExMjAwMDBaFw0zODAxMTUx\n" \
"MjAwMDBaMGExCzAJBgNVBAYTAlVTMRUwEwYDVQQKEwxEaWdpQ2VydCBJbmMxGTAXBgNVBAsTEHd3\n" \
"dy5kaWdpY2VydC5jb20xIDAeBgNVBAMTF0RpZ2lDZXJ0IEdsb2JhbCBSb290IEcyMIIBIjANBgkq\n" \
"hkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAuzfNNNx7a8myaJCtSnX/RrohCgiN9RlUyfuI2/Ou8jqJ\n" \
"kTx65qsGGmvPrC3oXgkkRLpimn7Wo6h+4FR1IAWsULecYxpsMNzaHxmx1x7e/dfgy5SDN67sH0NO\n" \
"3Xss0r0upS/kqbitOtSZpLYl6ZtrAGCSYP9PIUkY92eQq2EGnI/yuum06ZIya7XzV+hdG82MHauV\n" \
"BJVJ8zUtluNJbd134/tJS7SsVQepj5WztCO7TG1F8PapspUwtP1MVYwnSlcUfIKdzXOS0xZKBgyM\n" \
"UNGPHgm+F6HmIcr9g+UQvIOlCsRnKPZzFBQ9RnbDhxSJITRNrw9FDKZJobq7nMWxM4MphQIDAQAB\n" \
"o0IwQDAPBgNVHRMBAf8EBTADAQH/MA4GA1UdDwEB/wQEAwIBhjAdBgNVHQ4EFgQUTiJUIBiV5uNu\n" \
"5g/6+rkS7QYXjzkwDQYJKoZIhvcNAQELBQADggEBAGBnKJRvDkhj6zHd6mcY1Yl9PMWLSn/pvtsr\n" \
"F9+wX3N3KjITOYFnQoQj8kVnNeyIv/iPsGEMNKSuIEyExtv4NeF22d+mQrvHRAiGfzZ0JFrabA0U\n" \
"WTW98kndth/Jsw1HKj2ZL7tcu7XUIOGZX1NGFdtom/DzMNU+MeKNhJ7jitralj41E6Vf8PlwUHBH\n" \
"QRFXGU7Aj64GxJUTFy8bJZ918rGOmaFvE7FBcf6IKshPECBV1/MUReXgRPTqh5Uykw7+U0b6LJ3/\n" \
"iyK5S9kJRaTepLiaWN0bfVKfjllDiIGknibVb63dDcY3fe0Dkhvld1927jyNxF1WW6LZZm6zNTfl\n" \
"MrY=\n" \
"-----END CERTIFICATE-----\n";

// ==========================================
// SWITCHBOT BLE PROTOCOL
// ==========================================
static BLEUUID switchBotServiceUUID("cba20d00-224d-11e6-9fb8-0002a5d5c51b");
static BLEUUID switchBotCharUUID("cba20002-224d-11e6-9fb8-0002a5d5c51b");
const uint8_t SWITCHBOT_PRESS_CMD[] = {0x57, 0x01, 0x00};

WiFiClientSecure secureClient;
PubSubClient mqttClient(secureClient);

void syncNTPTime() {
  Serial.print("Syncing clock via SNTP for TLS validation");
  configTime(8 * 3600, 0, "pool.ntp.org", "time.google.com");

  time_t now = time(nullptr);
  int retry = 0;
  while (now < 100000 && retry < 30) {
    delay(500);
    Serial.print(".");
    now = time(nullptr);
    retry++;
  }
  
  if (now > 100000) {
    Serial.println("\nClock successfully synced!");
    struct tm timeinfo;
    gmtime_r(&now, &timeinfo);
    Serial.print("Current UTC Time: ");
    Serial.println(asctime(&timeinfo));
  } else {
    Serial.println("\nWarning: SNTP sync timed out. TLS temporal validation may fail.");
  }
}

bool triggerSwitchBot(const char* macStr) {
  BLEAddress pAddress(macStr, BLE_ADDR_TYPE_RANDOM);
  BLEClient* pClient = BLEDevice::createClient();

  if (!pClient->connect(pAddress)) {
    Serial.println("Failed to connect to SwitchBot BLE");
    delete pClient;
    return false;
  }

  BLERemoteService* pRemoteService = pClient->getService(switchBotServiceUUID);
  if (pRemoteService == nullptr) {
    pClient->disconnect();
    delete pClient;
    return false;
  }

  BLERemoteCharacteristic* pRemoteChar = pRemoteService->getCharacteristic(switchBotCharUUID);
  if (pRemoteChar == nullptr) {
    pClient->disconnect();
    delete pClient;
    return false;
  }

  pRemoteChar->writeValue((uint8_t*)SWITCHBOT_PRESS_CMD, sizeof(SWITCHBOT_PRESS_CMD), true);
  Serial.println("SwitchBot Actuated Successfully!");

  delay(400);
  pClient->disconnect();
  delete pClient;
  return true;
}

void mqttCallback(char* topic, byte* payload, unsigned int length) {
  String topicStr = String(topic);
  topicStr.toLowerCase(); // Prevents case-mismatch between Python and C++
  
  String userId = "";
  for (unsigned int i = 0; i < length; i++) {
    userId += (char)payload[i];
  }

  String targetMachine = "Unknown";
  if (topicStr.endsWith("washera")) targetMachine = "Washer A";
  else if (topicStr.endsWith("washerb")) targetMachine = "Washer B";
  else if (topicStr.endsWith("dryera")) targetMachine = "Dryer A";
  else if (topicStr.endsWith("dryerb")) targetMachine = "Dryer B";

  // Reflect command received on OLED
  updateOLED("Active (TLS)", targetMachine, "UID: " + userId);
  
  Serial.println("Inbound command on: " + topicStr + " for UID: " + userId);
  String replyMsg = "";

  if (topicStr.endsWith("washera") || topicStr.endsWith("chopewash-washer-a")) {
    if (triggerSwitchBot(MAC_WASHER_A)) replyMsg = userId + ":✅ Washer A started successfully!";
    else replyMsg = userId + ":❌ Error: Could not actuate Washer A.";
  } 
  else if (topicStr.endsWith("washerb") || topicStr.endsWith("chopewash-washer-b")) {
    if (triggerSwitchBot(MAC_WASHER_B)) replyMsg = userId + ":✅ Washer B started successfully!";
    else replyMsg = userId + ":❌ Error: Could not actuate Washer B.";
  }
  else if (topicStr.endsWith("dryera") || topicStr.endsWith("chopewash-dryer-a")) {
    if (triggerSwitchBot(MAC_DRYER_A)) replyMsg = userId + ":✅ Dryer A started successfully!";
    else replyMsg = userId + ":❌ Error: Could not actuate Dryer A.";
  }
  else if (topicStr.endsWith("dryerb") || topicStr.endsWith("chopewash-dryer-b")) {
    if (triggerSwitchBot(MAC_DRYER_B)) replyMsg = userId + ":✅ Dryer B started successfully!";
    else replyMsg = userId + ":❌ Error: Could not actuate Dryer B.";
  }
  updateOLED("Active (TLS)", targetMachine, "UID: " + userId);

 if (replyMsg.length() > 0) {
    mqttClient.publish(feedback_topic, replyMsg.c_str());
    updateOLED("Active (TLS)", targetMachine, "Actuation OK!");
  }
}

void setup() {
  Serial.begin(115200);

  // Initialize I2C OLED
  if (!display.begin(SSD1306_SWITCHCAPVCC, SCREEN_ADDRESS)) {
    Serial.println("SSD1306 allocation failed. Check I2C wiring.");
  } else {
    display.clearDisplay();
    updateOLED("Connecting WiFi...", "None", "Standby");
  }

  BLEDevice::init("ESP32_Laundry_Bridge");

  WiFi.disconnect(true);
  WiFi.mode(WIFI_STA);

#if USE_ENTERPRISE_WIFI
  // --- CAMPUS WPA2-ENTERPRISE BOOT ---
  Serial.print("Connecting to Enterprise Network (" EAP_SSID ")");
  esp_wifi_sta_wpa2_ent_set_identity((uint8_t *)EAP_IDENTITY, strlen(EAP_IDENTITY));
  esp_wifi_sta_wpa2_ent_set_username((uint8_t *)EAP_USERNAME, strlen(EAP_USERNAME));
  esp_wifi_sta_wpa2_ent_set_password((uint8_t *)EAP_PASSWORD, strlen(EAP_PASSWORD));
  esp_wifi_sta_wpa2_ent_enable();
  WiFi.begin(EAP_SSID);
#else
  // --- HOME WPA2-PERSONAL BOOT ---
  Serial.print("Connecting to Home Wi-Fi (" HOME_SSID ")");
  WiFi.begin(HOME_SSID, HOME_PASSWORD);
#endif

  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  updateOLED("WiFi OK | Sync NTP", "None", "Waiting TLS");
  Serial.println("\nWi-Fi Connected! IP: " + WiFi.localIP().toString());

  // 1. Sync clock for X.509 date validity checks
  syncNTPTime();

  // 2. Load Root CA trust anchor
  secureClient.setCACert(adafruit_ca_cert);

  mqttClient.setServer(mqtt_server, mqtt_port);
  mqttClient.setCallback(mqttCallback);
}

void loop() {
  if (!mqttClient.connected()) {
    while (!mqttClient.connected()) {
      Serial.print("Connecting to Adafruit IO over verified TLS...");
      
      if (mqttClient.connect("ESP32_Chope_Client", IO_USERNAME, IO_KEY)) {
        Serial.println(" Connected & Verified!");
        mqttClient.subscribe(SECRET_IO_USER "/feeds/washera");
        mqttClient.subscribe(SECRET_IO_USER "/feeds/washerb");
        mqttClient.subscribe(SECRET_IO_USER "/feeds/dryera");
        mqttClient.subscribe(SECRET_IO_USER "/feeds/dryerb");
      } else {
        Serial.print(" Handshake Failed (rc=");
        Serial.print(mqttClient.state());
        Serial.print("). Free Heap: ");
        Serial.print(ESP.getFreeHeap());
        
        char errBuf[128];
        secureClient.lastError(errBuf, sizeof(errBuf));
        Serial.print(" | SSL Error: ");
        Serial.println(errBuf);
        
        delay(5000);
      }
    }
  }
  mqttClient.loop();
}