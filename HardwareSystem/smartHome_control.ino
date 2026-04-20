#include <ESP32Servo.h>
#include <WiFi.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>
#include <Preferences.h>
#include <SPIFFS.h>
#include <WebServer.h>
#include <HTTPClient.h>
#include <WiFiClient.h>
#include <DNSServer.h>

WebServer server(80);
Preferences preferences;
// =============== Cấu hình Kết Nối ==================
String ssid = "";
String password = "";
const char* mqtt_server = "14.225.224.167";
const char* mqtt_id = "esp32_control";
const long port = 1881;
const char* topic_state = "home/status/device"; 
const char* topic_subscribe = "home/command"; 

WiFiClient espClient;
PubSubClient client(espClient);

// =============== Biến Toàn Cục & Cấu hình Động ==================
#define MAX_DEVICES 20
int current_device_count = 0;

#define Relay_ON HIGH
#define Relay_OFF LOW
#define DOOR_CLOSED_ANGLE  90    
#define DOOR_OPEN_ANGLE    0
DNSServer dnsServer;
Servo door_servo; // Hỗ trợ 1 Servo cho cửa sổ
uint8_t active_door_lock_pin = 255; // Lưu chân của khóa cửa để tự động tắt
int active_door_index = -1;
unsigned long lastMqttAttempt = 0;
unsigned long doorUnlockTime = 0;
bool isDoorUnlocked = false;
const unsigned long DOOR_UNLOCK_DURATION = 15000; // Tự khóa sau 5s

// Phân loại phần cứng
enum HardwareType { 
  TYPE_RELAY = 0,       
  TYPE_SERVO = 1,       
  TYPE_DOOR_LOCK = 2    
};

struct DeviceMap {
  String location;
  String device;
  uint8_t pin;
  HardwareType type;
};

DeviceMap deviceTable[MAX_DEVICES]; // Khởi tạo mảng động

// Khai báo hàm
void connectWiFi();
void connectMQTT();
void startConfigPortal();
void loadWiFiConfig();
void saveWiFiConfig(String newSsid, String newPassword);

// ==========================================================
// 1. ĐỌC CẤU HÌNH TỪ SPIFFS VÀ CÀI ĐẶT CHÂN (PIN)
// ==========================================================
bool loadConfigFromSPIFFS() {
  if (!SPIFFS.exists("/config.json")) {
    Serial.println(" Chưa có file cấu hình SPIFFS. Cần tải từ API...");
    return false;
  }
  
  File file = SPIFFS.open("/config.json", "r");
  DynamicJsonDocument doc(1024);
  DeserializationError error = deserializeJson(doc, file);
  file.close();

  if (error) {
    Serial.println("Lỗi giải mã file config.json");
    return false;
  }

  JsonArray array = doc.as<JsonArray>();
  current_device_count = 0;
  
  Serial.println("\n--- BẢNG CẤU HÌNH PHẦN CỨNG ---");
  for (JsonObject obj : array) {
    if (current_device_count >= MAX_DEVICES) break;
    
    deviceTable[current_device_count].location = obj["l"].as<String>();
    deviceTable[current_device_count].device = obj["d"].as<String>();
    deviceTable[current_device_count].pin = obj["p"].as<uint8_t>();
    deviceTable[current_device_count].type = (HardwareType)obj["t"].as<int>();

    uint8_t pin = deviceTable[current_device_count].pin;
    HardwareType hwType = deviceTable[current_device_count].type;

  
    if (hwType == TYPE_SERVO) {
      door_servo.attach(pin);
      door_servo.write(DOOR_CLOSED_ANGLE);
      Serial.printf(" [SERVO] %s - %s tại Pin %d\n", deviceTable[current_device_count].device.c_str(), deviceTable[current_device_count].location.c_str(), pin);
    } else {
      pinMode(pin, OUTPUT);
      digitalWrite(pin, Relay_OFF);
      Serial.printf(" [RELAY] %s - %s tại Pin %d\n", deviceTable[current_device_count].device.c_str(), deviceTable[current_device_count].location.c_str(), pin);
    }
    
    current_device_count++;
  }
  Serial.println("-------------------------------\n");
  return true;
}

// ==========================================================
// 2. GỌI API ĐỂ TẢI CẤU HÌNH MỚI (FIXED)
// ==========================================================
void fetchConfigFromAPI() {
  if (WiFi.status() == WL_CONNECTED) {
    WiFiClient client;
    HTTPClient http;
    
    // Khai báo URL rõ ràng
    String serverUrl = "http://14.225.224.167:8000/api/esp_config";
    
    Serial.println(" Đang đợi mạng ổn định (2s)..");
    delay(2000); 
    
    Serial.print("Đang tải cấu hình từ: ");
    Serial.println(serverUrl);

    // BẮT ĐẦU KẾT NỐI (Dùng WiFiClient để tránh lỗi -1)
    if (http.begin(client, serverUrl)) { 
      int httpCode = http.GET();
      
      if (httpCode == HTTP_CODE_OK) {
        // Mở file trên SPIFFS để ghi dữ liệu trực tiếp từ Stream
        File file = SPIFFS.open("/config.json", "w");
        if (file) {
          http.writeToStream(&file);
          file.close();
          Serial.println("Tải thành công! ESP32 sẽ khởi động lại sau 2s...");
          delay(2000);
          ESP.restart(); 
        } else {
          Serial.println("Lỗi: Không thể mở file SPIFFS để ghi");
        }
      } else {
        Serial.printf(" Lỗi API: HTTP Code %d (Lỗi: %s)\n", httpCode, http.errorToString(httpCode).c_str());
      }
      http.end();
    } else {
      Serial.println("Không thể khởi tạo kết nối HTTP!");
    }
  } else {
    Serial.println("WiFi chưa kết nối, không thể tải cấu hình.");
  }
}

// ==========================================================
// 3. XỬ LÝ LỆNH TỪ MQTT (ĐỘNG 100%)
// ==========================================================
void callback(char* topic, byte* payload, unsigned int length) {
  String messageTemp;
  for (int i = 0; i < length; i++) messageTemp += (char)payload[i];
  
  StaticJsonDocument<256> doc;
  if (deserializeJson(doc, messageTemp)) return;

  // Lấy dữ liệu dạng String
  String action   = doc["action"] | "";   
  String device   = doc["device"] | "";   
  String status   = doc["status"] | "";   
  String location = doc["location"] | ""; 
  String user     = doc["user"] | "Unknown";
  String role     = doc["role"] | "User";
  if (device == "door" && location == "bedroom") {
      device = "window"; 
  }
  if (action == "reload_config") {
    Serial.println(" [SYSTEM] Nhận lệnh cập nhật phần cứng. Đang xóa cấu hình cũ...");
    SPIFFS.remove("/config.json"); // Xóa file cũ
    delay(500);
    ESP.restart(); 
    return;
  }
  bool turn_on = (status == "ON" || action == "open");
  int target_index = -1;

  // Dò tìm thiết bị trong mảng RAM
  for(int i = 0; i < current_device_count; i++){
    if(deviceTable[i].location == location && deviceTable[i].device == device){
      target_index = i;
      break;  
    }
  }

  // Ra lệnh cho phần cứng
  if(target_index != -1){
    uint8_t pin = deviceTable[target_index].pin;
    HardwareType hw_type = deviceTable[target_index].type;

    switch (hw_type) {
      case TYPE_RELAY:
        digitalWrite(pin, turn_on ? Relay_ON : Relay_OFF);
        Serial.printf(" Đã %s %s tại %s\n", turn_on ? "BẬT" : "TẮT", device.c_str(), location.c_str());
        break;

      case TYPE_SERVO:
        door_servo.write(turn_on ? DOOR_OPEN_ANGLE : DOOR_CLOSED_ANGLE);
        Serial.printf(" Đã %s %s tại %s\n", turn_on ? "Đóng" : "Mở", device.c_str(), location.c_str());
        break;

      case TYPE_DOOR_LOCK:
        if (turn_on) { 
          digitalWrite(pin, Relay_ON); 
          isDoorUnlocked = true;               
          doorUnlockTime = millis();
          active_door_lock_pin = target_index; 
          Serial.printf(" %s (%s) đã mở Khóa cửa %s\n", user.c_str(), role.c_str(), location.c_str());
        }else {
          digitalWrite(pin, Relay_OFF);
          isDoorUnlocked = false;
          Serial.printf(" Đã khóa cửa %s bằng WebUI/AI\n", location.c_str());
        }
    }

    // Publish trạng thái cập nhật lên MQTT
    StaticJsonDocument<128> docResponse;
    docResponse["location"] = location;
    docResponse["device"] = device;
    docResponse["status"] = turn_on ? "ON" : "OFF";
    char buffer[128];
    serializeJson(docResponse, buffer);
    client.publish(topic_state, buffer);

  } else {
    Serial.printf(" Lệnh gửi đến %s ở %s nhưng thiết bị chưa được cài đặt!\n", device.c_str(), location.c_str());
  }
}

// ==========================================================
// 4. SETUP & LOOP
// ==========================================================
void setup() {
  Serial.begin(115200);
  ESP32PWM::allocateTimer(0);
  ESP32PWM::allocateTimer(1);
  ESP32PWM::allocateTimer(2);
  ESP32PWM::allocateTimer(3);
  door_servo.setPeriodHertz(50);
  if (!SPIFFS.begin(true)) {
    Serial.println("Lỗi Mount SPIFFS");
    return;
  }
  // SPIFFS.format();

  connectWiFi();

  // Load cấu hình. Nếu rỗng thì gọi mạng tải về
  if (!loadConfigFromSPIFFS()) {
    fetchConfigFromAPI();
  }

  client.setServer(mqtt_server, port);
  client.setCallback(callback); 
}

void loop() {
  server.handleClient();
  if(!client.connected()){
    if(millis() - lastMqttAttempt > 5000){
      lastMqttAttempt = millis();
      connectMQTT();
    }
  } else {
    client.loop();
  }
  
 if (isDoorUnlocked && (millis() - doorUnlockTime >= DOOR_UNLOCK_DURATION)) {
    digitalWrite(active_door_lock_pin, Relay_OFF); 
    isDoorUnlocked = false;
    Serial.println(" Đã tự động KHÓA cửa chính.");
    if (active_door_index != -1) {
      StaticJsonDocument<128> docResponse;
      docResponse["location"] = deviceTable[active_door_index].location;
      docResponse["device"] = deviceTable[active_door_index].device;
      docResponse["status"] = "OFF";
      
      char buffer[128];
      serializeJson(docResponse, buffer);
      client.publish(topic_state, buffer); 
      active_door_index = -1; 
    }
  }
}

// ==========================================================
// CÁC HÀM WIFI / CAPTIVE PORTAL (Giữ nguyên của bạn)
// ==========================================================
void startConfigPortal() {
  WiFi.mode(WIFI_AP);
  WiFi.softAP("ESP_Control", "12345678");
  dnsServer.start(53, "*", WiFi.softAPIP());
  Serial.print("AP IP address: ");
  Serial.println(WiFi.softAPIP());

  server.on("/", HTTP_GET, []() {
    server.send(200, "text/html", R"rawliteral(
      <!DOCTYPE html>
      <html>
      <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Cấu hình ESP32 WiFi</title>
        <style>
          body { font-family: Arial, sans-serif; background: #f2f2f2; margin: 0; padding: 0; }
          .container { max-width: 400px; margin: 30px auto; background: #fff; padding: 20px; border-radius: 12px; box-shadow: 0 0 10px rgba(0,0,0,0.1); }
          h2 { text-align: center; color: #333; }
          input[type="text"], input[type="password"] { width: 100%; padding: 10px; margin: 8px 0 16px 0; border: 1px solid #ccc; border-radius: 6px; box-sizing: border-box; }
          input[type="submit"] { width: 100%; background-color: #4CAF50; color: white; padding: 12px; border: none; border-radius: 6px; cursor: pointer; font-size: 16px; font-weight: bold;}
          input[type="submit"]:hover { background-color: #45a049; }
        </style>
      </head>
      <body>
        <div class="container">
        <h2>Cấu hình Mạng WiFi</h2>
        <form action="/save" method="get">
          <label>Tên WiFi (SSID):</label>
          <input type="text" name="ssid" required>
          <label>Mật khẩu (Password):</label>
          <input type="password" name="pass">
          <input type="submit" value="Lưu & Khởi động lại">
        </form>
        </div>
      </body>
      </html>
    )rawliteral");
  });

  server.on("/save", HTTP_GET, []() {
    String newSsid = server.arg("ssid");
    String newPass = server.arg("pass");
    if (newSsid != "") {
      saveWiFiConfig(newSsid, newPass);
      server.send(200, "text/html", "Saved. Restarting...");
      delay(1000);
      ESP.restart();
    } else {
      server.send(200, "text/html", "Invalid input.");
    }
  });
  server.onNotFound([]() {
    server.sendHeader("Location", "http://" + WiFi.softAPIP().toString(), true);
    server.send(302, "text/plain", "");
  });

  server.begin();
  unsigned long startTime = millis();
  
  while (true) {
    dnsServer.processNextRequest(); 
    
    server.handleClient();
    delay(10);
    if (millis() - startTime > 300000) { ESP.restart(); } 
  }
}
void loadWiFiConfig() {
  preferences.begin("wifi", false); 
  ssid = preferences.getString("ssid", ""); 
  password = preferences.getString("password", "");
  preferences.end();
}
void saveWiFiConfig(String newSsid, String newPassword) {
  preferences.begin("wifi", false);
  preferences.putString("ssid", newSsid);
  preferences.putString("password", newPassword);
  preferences.end();
}
void connectWiFi() {
  loadWiFiConfig();  
  if(ssid == "") startConfigPortal();
  Serial.print("Connecting WiFi: " + ssid);
  WiFi.begin(ssid.c_str(), password.c_str());
  int retry = 0;
  while (WiFi.status() != WL_CONNECTED && retry < 20) {
    delay(500); Serial.print("."); retry++;
  }
  if (WiFi.status() == WL_CONNECTED) {
    Serial.println("\nWiFi OK! IP: " + WiFi.localIP().toString());
  } else {
    Serial.println("\nWiFi Lỗi! Bật Portal...");
    startConfigPortal();
  }
}
void connectMQTT() {
  Serial.print("Connecting MQTT...");
  if (client.connect(mqtt_id)) {
    Serial.println("OK");
    client.subscribe(topic_subscribe);
  } else {
    Serial.print("Fail, rc=");
    Serial.println(client.state());
  }
}