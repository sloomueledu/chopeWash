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

// ==========================================
// ADAFRUIT IO & MQTT CONFIGURATION
// ==========================================
const char* mqtt_server    = "io.adafruit.com";
const int   mqtt_port      = 8883;
const char* IO_USERNAME    = SECRET_IO_USER;
const char* IO_KEY         = SECRET_IO_KEY;
const char* feedback_topic = SECRET_IO_USER "/feeds/chopewash-feedback";

// ==========================================
// CRYPTOGRAPHIC TRUST ANCHOR (ISRG Root X1)
// ==========================================
const char* adafruit_ca_cert = \
"-----BEGIN CERTIFICATE-----\n" \
"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\n" \
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
  BLEAddress pAddress(macStr, BLE_ADDR_RANDOM);
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

  if (replyMsg.length() > 0) {
    mqttClient.publish(feedback_topic, replyMsg.c_str());
  }
}

void setup() {
  Serial.begin(115200);
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
        
        // Valid explicit subscriptions to avoid broker rejection
        mqttClient.subscribe(SECRET_IO_USER "/feeds/washera");
        mqttClient.subscribe(SECRET_IO_USER "/feeds/washerb");
        mqttClient.subscribe(SECRET_IO_USER "/feeds/dryera");
        mqttClient.subscribe(SECRET_IO_USER "/feeds/dryerb");
      } else {
        Serial.print(" Handshake Failed (rc=");
        Serial.print(mqttClient.state());
        Serial.println("). Retrying in 5s...");
        delay(5000);
      }
    }
  }
  mqttClient.loop();
}