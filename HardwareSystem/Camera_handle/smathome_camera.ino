#define CAMERA_MODEL_ESP32S3_EYE 
#include "board_config.h"
#include "esp_camera.h"

#include <WiFi.h>
#include <WebServer.h>
#include <DNSServer.h>
#include <Preferences.h>
#include <Wire.h>
#include <Adafruit_SSD1306.h>
#include <WebSocketsClient.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>

// ================= CẤU HÌNH CHÂN & THÔNG SỐ =================
#define RESET_BUTTON 0       // Nút Boot để reset WiFi
#define SCREEN_WIDTH 128
#define SCREEN_HEIGHT 64
#define I2C_SDA 1            // Chân OLED
#define I2C_SCL 2            // Chân OLED
#define TRIG_PIN 47          // Chân Siêu âm (Huy đổi theo thực tế nhé)
#define ECHO_PIN 48          // Chân Siêu âm 
#define DISTANCE_THRESHOLD 20 // cm: Khoảng cách kích hoạt camera
#define STREAM_TIMEOUT 10000   // ms: Tự tắt cam sau 10 giây không có người

// ================= CẤU HÌNH WEBSOCKET =================
const char* websocket_server = "14.225.224.167";
const uint16_t websocket_port = 8000;
const char* websocket_path = "/ws/camera_upload";
// Cấu hình MQTT
const char* mqtt_server = "14.225.224.167";
const int mqtt_port = 1881;
const char* mqtt_topic_force = "smarthome/camera/force";
const char* mqtt_topic_result = "smarthome/camera/result";

WebSocketsClient webSocket;
WiFiClient espClient;
PubSubClient mqttClient(espClient);
Adafruit_SSD1306 display(SCREEN_WIDTH, SCREEN_HEIGHT, &Wire, -1);
WebServer server(80);
DNSServer dnsServer;
Preferences preferences;

String ssid, password;
unsigned long lastReconnectAttempt = 0;
unsigned long lastPersonDetected = 0;
bool isStreaming = false;
int detectCount = 0; // Bộ đếm chống nhiễu siêu âm
bool forceStream = false;

unsigned long welcomeStartTime = 0;   
bool isShowingWelcome = false;        
const unsigned long WELCOME_DURATION = 5000; 
// ================= HÀM ĐO KHOẢNG CÁCH =================
long getDistance() {
  digitalWrite(TRIG_PIN, LOW);
  delayMicroseconds(2);
  digitalWrite(TRIG_PIN, HIGH);
  delayMicroseconds(10);
  digitalWrite(TRIG_PIN, LOW);
  
  long duration = pulseIn(ECHO_PIN, HIGH, 20000); 
  if (duration == 0) return 999;
  return duration * 0.034 / 2;
}
void mqttCallback(char* topic, byte* payload, unsigned int length) {
  String message;
  for (int i = 0; i < length; i++) message += (char)payload[i];
  
  Serial.println("MQTT Message: " + message);
  
  // Logic: {"action": "START"} hoặc {"action": "STOP"}
  if (message.indexOf("START") >= 0) {
    forceStream = true;
    isStreaming = true;
    updateOLED("REMOTE VIEW", "Active", "Manual Mode");
  } else if (message.indexOf("STOP") >= 0) {
    forceStream = false;
    isStreaming = false;
    updateOLED("STATUS: IDLE", "Waiting...", "");
  }
  // 2. LOGIC HIỂN THỊ TÊN VÀ ROLE (Dùng JSON)
  StaticJsonDocument<200> doc;
  DeserializationError error = deserializeJson(doc, message);
  
  if (!error && doc.containsKey("user")) {
    String userName = doc["user"] | "Unknown";
    String userRole = doc["role"] | "Guest";
    
    // Bật trạng thái chào mừng
    isShowingWelcome = true;
    welcomeStartTime = millis(); 
    
    updateOLED("WELCOME HOME!", userName, "Role: " + userRole);
    Serial.println("Hien loi chao: " + userName);
  }
}
// ================= HÀM HIỂN THỊ OLED =================
void updateOLED(String l1, String l2, String l3) {
  display.clearDisplay();
  display.setTextSize(1);
  display.setTextColor(SSD1306_WHITE);
  display.setCursor(0, 0);  display.println("--- SMART VISION ---");
  display.setCursor(0, 18); display.println(l1);
  display.setCursor(0, 33); display.println(l2);
  display.setCursor(0, 48); display.println(l3);
  display.display();
}

// ================= LƯU/ĐỌC WIFI =================
void loadConfig() {
  preferences.begin("wifi", true);
  ssid = preferences.getString("ssid", "");
  password = preferences.getString("pass", "");
  preferences.end();
}

// ================= CAPTIVE PORTAL (WIFI SETUP) =================
void startConfigPortal() {
  updateOLED("CONFIG MODE", "Connect: ESP32-SETUP", "Pass: 12345678");
  Serial.println("Mở trạm phát WiFi (AP Mode)...");

  WiFi.mode(WIFI_AP);
  WiFi.softAP("ESP32-SETUP", "12345678");
  delay(500);
  dnsServer.start(53, "*", WiFi.softAPIP());
  
  server.on("/", HTTP_GET, []() {
    String html = "<html><head><meta name='viewport' content='width=device-width,initial-scale=1.0'>"
                  "<style>body{font-family:sans-serif;padding:20px;} input,button{width:100%;padding:10px;margin:10px 0;}</style></head>"
                  "<body><h2>Cai dat WiFi</h2><form action='/save'>"
                  "SSID:<input name='ssid' required> PASS:<input name='pass' type='password' required>"
                  "<button type='submit'>Luu & Khoi dong lai</button></form></body></html>";
    server.send(200, "text/html", html);
  });

  server.on("/save", HTTP_GET, []() {
    preferences.begin("wifi", false);
    preferences.putString("ssid", server.arg("ssid"));
    preferences.putString("pass", server.arg("pass"));
    preferences.end();
    server.send(200, "text/html", "<h2>Da luu! Dang khoi dong lai...</h2>");
    delay(2000);
    ESP.restart();
  });

  server.onNotFound([]() {
    server.sendHeader("Location", String("http://") + WiFi.softAPIP().toString(), true);
    server.send(302, "text/plain", "");
  });

  server.begin();
  while (true) {
    dnsServer.processNextRequest(); 
    server.handleClient();          
    delay(10);
  }
}

// ================= KHỞI TẠO CAMERA =================
bool initCamera() {
  camera_config_t config;
  config.ledc_channel = LEDC_CHANNEL_0;
  config.ledc_timer = LEDC_TIMER_0;
  config.pin_d0 = Y2_GPIO_NUM;
  config.pin_d1 = Y3_GPIO_NUM;
  config.pin_d2 = Y4_GPIO_NUM;
  config.pin_d3 = Y5_GPIO_NUM;
  config.pin_d4 = Y6_GPIO_NUM;
  config.pin_d5 = Y7_GPIO_NUM;
  config.pin_d6 = Y8_GPIO_NUM;
  config.pin_d7 = Y9_GPIO_NUM;
  config.pin_xclk = XCLK_GPIO_NUM;
  config.pin_pclk = PCLK_GPIO_NUM;
  config.pin_vsync = VSYNC_GPIO_NUM;
  config.pin_href = HREF_GPIO_NUM;
  config.pin_sccb_sda = SIOD_GPIO_NUM;
  config.pin_sccb_scl = SIOC_GPIO_NUM;
  config.pin_pwdn = PWDN_GPIO_NUM;
  config.pin_reset = RESET_GPIO_NUM;
  config.xclk_freq_hz =10000000;
  config.pixel_format = PIXFORMAT_JPEG;

  if (psramFound()) {
    config.frame_size = FRAMESIZE_VGA; // Độ phân giải 640x480 cho FaceID
    config.jpeg_quality = 12;
    config.fb_count = 2;
    config.fb_location = CAMERA_FB_IN_PSRAM;
  } else {
    config.frame_size = FRAMESIZE_QVGA;
    config.jpeg_quality = 20;
    config.fb_count = 1;
    config.fb_location = CAMERA_FB_IN_DRAM;
  }

  esp_err_t err = esp_camera_init(&config);
  if(err != ESP_OK) return false;
  sensor_t * s = esp_camera_sensor_get();
  // Chỉnh lại nếu ảnh bị ngược
  // s->set_hmirror(s, 1);
  // s->set_vflip(s, 1);
  return true;
}

void webSocketEvent(WStype_t type, uint8_t * payload, size_t length) {
  switch(type) {
    case WStype_CONNECTED:
      Serial.println(" WS Connected");
      break;
    case WStype_DISCONNECTED:
      Serial.println(" WS Disconnected");
      break;
    case WStype_ERROR:
      Serial.println("WS Error");
      break;
  }
}
// ================= SETUP =================
void setup() {
  Serial.begin(115200);
  Wire.begin(I2C_SDA, I2C_SCL);

  if(!display.begin(SSD1306_SWITCHCAPVCC, 0x3C)) Serial.println("OLED Fail");

  pinMode(RESET_BUTTON, INPUT_PULLUP);
  pinMode(TRIG_PIN, OUTPUT);
  pinMode(ECHO_PIN, INPUT);
  updateOLED("BOOTING...", "Check hardware", "");

  // Nút Reset WiFi
  if (digitalRead(RESET_BUTTON) == LOW) {
    updateOLED("RESETTING WIFI...", "Clear memory", "");
    preferences.begin("wifi", false);
    preferences.clear();
    preferences.end();
    delay(2000);
    ESP.restart();
  }

  if (!initCamera()) {
    updateOLED("CAMERA FAIL", "Check hardware!", "");
    while (true) { delay(1000); }
  }

  loadConfig();
  if (ssid == "") {
    startConfigPortal();
  }
  // Kết nối WiFi (Tối ưu)
  WiFi.mode(WIFI_STA);
  WiFi.begin(ssid.c_str(), password.c_str());
  int retry = 0;
  while (WiFi.status() != WL_CONNECTED && retry < 20) {
    delay(250);
    retry++;
  }
  
  if (WiFi.status() != WL_CONNECTED) {
      startConfigPortal(); // Nếu pass sai, mở lại cấu hình
  }

  String ip = WiFi.localIP().toString();
  updateOLED("STATUS: ONLINE", "IP: " + ip, "System Ready");
  
  webSocket.begin(websocket_server, websocket_port, websocket_path);
  webSocket.setReconnectInterval(5000);
  webSocket.onEvent(webSocketEvent);
  mqttClient.setServer(mqtt_server, mqtt_port);
  mqttClient.setCallback(mqttCallback);
  Serial.println("Chờ cảm biến ổn định...");
  delay(1000); // CHỐNG NHIỄU KHỞI ĐỘNG
}

// ================= LOOP =================
void loop() {
  webSocket.loop();
  if (!mqttClient.connected()) {
    if (mqttClient.connect("ESP32_Camera_Client")) {
      mqttClient.subscribe(mqtt_topic_force);
      mqttClient.subscribe(mqtt_topic_result);
    }
  }
  mqttClient.loop();
  if (WiFi.status() != WL_CONNECTED) {
    if (millis() - lastReconnectAttempt >= 10000) {
      lastReconnectAttempt = millis();
      WiFi.begin(ssid.c_str(), password.c_str());
    }
    return;
  }

  if (isShowingWelcome) {
    if (millis() - welcomeStartTime >= WELCOME_DURATION) {
      isShowingWelcome = false; 
      if (isStreaming) {
        updateOLED("MOTION DETECTED", "Streaming Active", "");
      } else {
        updateOLED("STATUS: IDLE", "Waiting...", "");
      }
    }
    
  }
  // 2. Logic siêu âm (Chỉ chạy khi KHÔNG đang hiện lời chào)
  else{
      long distance = getDistance();
      if (distance < DISTANCE_THRESHOLD) {
        detectCount++; 
        if (detectCount >= 3) {
          lastPersonDetected = millis();
          if (!isStreaming) {
            isStreaming = true;
            updateOLED("MOTION DETECTED", "Streaming Active", "");
          }
        }
      } else {
        detectCount = 0;
      }

      // Tự tắt stream sau timeout
      if (isStreaming && (millis() - lastPersonDetected > STREAM_TIMEOUT)) {
        isStreaming = false;
        updateOLED("STATUS: IDLE", "Waiting...", "");
      }
  }

  // --- LUỒNG GỬI ẢNH WEBSOCKET ---
  if (isStreaming||forceStream) {
    if (webSocket.isConnected()) {
      camera_fb_t *fb = esp_camera_fb_get();
      if (fb) {
        webSocket.sendBIN(fb->buf, fb->len);
        esp_camera_fb_return(fb); 
        delay(80); 
      }
    } else {
      static unsigned long lastWsRetry = 0;
      if (millis() - lastWsRetry > 2000) {
        Serial.println("🔄 Reconnecting WS...");
        lastWsRetry = millis();
      }
    }
  } else {
    delay(100); 
  }
}