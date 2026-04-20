//==========================  Import thư viện===================
#include <WiFi.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>
#include <DHT.h>
#include <Wire.h>
#include "Adafruit_SGP30.h"
#include <BH1750.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include <Preferences.h>
#include <SPIFFS.h>
#include <WebServer.h>
#include <DNSServer.h>
// =================== Cáu hình kết nối========================
String ssid = "";
String password = "";

const char* mqtt_server="14.225.224.167";
const char*mqtt_id="esp32_sensor";
const uint16_t port=1881;
// topic theo dữ liệu từng phòng
const char* topic_bedroom  = "home/sensor/bedroom";
const char* topic_kitchen = "home/sensor/kitchen";
const char* topic_livingroom= "home/sensor/livingroom";
const char* topic_bathroom = "home/sensor/bathroom";
const char* topic_subscribe = "home/command";
WiFiClient espClient;
PubSubClient client(espClient);
WebServer server(80);
Preferences preferences;
DNSServer dnsServer;
QueueHandle_t dataQueue;
struct SensorData {
  char topic[50];
  char payload[256];
};
//===================Khai Báo Pin kết nối=====================
// Cảm biến nhiệt độ độ ẩm
#define DHTPinBedroom 15
#define DHTPinLivingroom 4
#define DHTPinKitchen 5
#define DHTType DHT11
DHT dhtbedroom(DHTPinBedroom,DHTType);
DHT dhtlivingroom(DHTPinLivingroom,DHTType);
DHT dhtKitchen(DHTPinKitchen,DHTType);
// Cảm biến khí gas
#define GasPinKitchen 34 
// Cảm biến từ trường Ky-024
#define DoorSensorBedroom 32
#define DoorSensorBathroom 33
#define DoorSensorLivingroom 25
// cảm biến ánh sáng phòng ngủ
#define LRDPIN 35
// cảm biến ánh sáng phòng khách
BH1750 lightMeter;
// Chân sda, sdl
#define I2C_SDA_PIN 21
#define I2C_SDL_PIN 22

#define SCREEN_WIDTH 128 
#define SCREEN_HEIGHT 64 
#define OLED_RESET  -1 
#define SCREEN_ADDRESS 0x3C 
Adafruit_SSD1306 display(SCREEN_WIDTH, SCREEN_HEIGHT, &Wire, OLED_RESET);
Adafruit_SGP30 sgp;

// =======Struct xử lí nhiễu==============
struct PirSensor{
  uint8_t pin;
  int filteredState = LOW;
  int lastRawState = LOW;
  unsigned long lastDebounceTime = 0;
  const unsigned long WARMUP_TIME = 30000; // 30 giây

 void init(uint8_t p){
   pin = p;
   pinMode(pin, INPUT); 
 }

 void update(){
   if(millis() < WARMUP_TIME) {
     filteredState = LOW; 
     return; 
   }

   int rawState = digitalRead(pin);
   if(rawState != lastRawState) {
     lastDebounceTime = millis(); 
   }
   if((millis() - lastDebounceTime) > 500){
     if(rawState != filteredState) {
     filteredState = rawState;
     }
   }
   lastRawState = rawState;
  }
};

PirSensor pirBedroom;
PirSensor pirKitchen;
PirSensor pirLiving;
PirSensor pirBathroom;
// =========== Timer==================
unsigned long previousMillis =0;
unsigned long lastSGP30Millis = 0; 
const long interval = 2000;
// ================cache============
float cache_tempBed = 0, cache_humiBed = 0;
float cache_tempKit = 0, cache_humiKit = 0;
float cache_tempLiv = 0, cache_humiLiv = 0;
int cache_tvoc =0, cache_eco2=400;
// hàm tính trung bình cảm biến 
int readAnalogAvg(uint8_t pin, int samples=10){
  long sum=0;
  for(int i=0; i<samples;i++){
   sum+=analogRead(pin);
   delay(2);
  }
  return sum/samples;
}
void connectWiFi();
void connectMQTT();
void startConfigPortal();
void loadWiFiConfig();
void saveWiFiConfig(String newSsid, String newPassword);
void callback(char* topic, byte* payload, unsigned int length){}
void MQTTTaskCode(void * pvParameters);
void SensorTaskCore(void * pvParameters);
// khai báo 2 task
TaskHandle_t TaskMQTT;
TaskHandle_t TaskSensors;
//=========================== hàm khởi tạo=================
void setup() {
  Serial.begin(115200);
  // khởi tạo chân cửa
  pinMode(DoorSensorBedroom, INPUT_PULLUP);
  pinMode(DoorSensorBathroom, INPUT_PULLUP);
  pinMode(DoorSensorLivingroom, INPUT_PULLUP);
// khởi tạo pir
  pirBedroom.init(13);
  pirLiving.init(14);
  pirKitchen.init(26);
  pirBathroom.init(27);
  // khởi tạo DHT
  dhtbedroom.begin();
  dhtlivingroom.begin();
  dhtKitchen.begin();
  pinMode(LRDPIN, INPUT);
  pinMode(GasPinKitchen, INPUT);
  Wire.begin(I2C_SDA_PIN,I2C_SDL_PIN);
  if (lightMeter.begin(BH1750::CONTINUOUS_HIGH_RES_MODE)) {
   Serial.println("BH1750 đã sẵn sàng!");
  } else {
   Serial.println("Không tìm thấy BH1750!");
  }

  if(!display.begin(SSD1306_SWITCHCAPVCC, SCREEN_ADDRESS)) {
   Serial.println(F("Lỗi: Không tìm thấy màn hình OLED"));
  
  } else {
   display.clearDisplay();    
   display.setTextSize(1);    
   display.setTextColor(SSD1306_WHITE); 
   display.setCursor(0, 0);
   display.println("SMART HOME");
   display.println("Đang kết nối WiFi...");
   display.display();
  }
  if (!sgp.begin()) 
   Serial.println("SGP30 not found!");
  connectWiFi();
  client.setServer(mqtt_server,port);
  client.setCallback(callback);
  client.setBufferSize(512);
  dataQueue = xQueueCreate(25, sizeof(SensorData));
  xTaskCreatePinnedToCore(MQTTTaskCode, "TaskMQTT", 10000, NULL, 1, &TaskMQTT, 0);
  xTaskCreatePinnedToCore(SensorTaskCore, "TaskSensors", 10000, NULL, 1, &TaskSensors, 1);
}

// ==============CORE 0=================
void MQTTTaskCode(void * pvParameters) {
  for (;;) {
    if (!client.connected()) connectMQTT();
    client.loop();
    server.handleClient();

    // Kiểm tra xem có dữ liệu nào trong hàng đợi không để gửi đi
    SensorData outgoing;
    if (xQueueReceive(dataQueue, &outgoing, 0) == pdPASS) {
      client.publish(outgoing.topic, outgoing.payload);
    }
    
    vTaskDelay(5 / portTICK_PERIOD_MS); 
  }
}
// hàm phụ
void pushToQueue(const char* topic, JsonDocument& doc) {
    SensorData data;
    strlcpy(data.topic, topic, sizeof(data.topic));
    serializeJson(doc, data.payload);
    
    // Đẩy vào hàng đợi, đợi tối đa 10ms nếu hàng đợi đầy
    if (xQueueSend(dataQueue, &data, pdMS_TO_TICKS(25)) != pdPASS) {
        Serial.println("Queue đầy, bỏ qua gói tin!");
    }
}
// =============CORE 1===================
void SensorTaskCore(void * pvParameters){
  Serial.print("Sensor task running on core");
  Serial.println(xPortGetCoreID());
  static int lastDoorLiv = -1;
  static int lastDoorBed=-1;
  static int lastDoorBath=-1;
  static int lastPirLiv = -1;
  static int lastPirBed = -1;
  static int lastPirBath = -1;
  static int lastPirKit = -1;

  for(;;){
    pirBedroom.update();
    pirBathroom.update();
    pirKitchen.update();
    pirLiving.update();
    unsigned long now=millis();
    unsigned long current=millis();
    // if (now - lastSGP30Millis > 1000) {
    // lastSGP30Millis = now;
    
    // }
      int doorBed=digitalRead(DoorSensorBedroom);
      int doorBath=digitalRead(DoorSensorBathroom);
      int doorLiving=digitalRead(DoorSensorLivingroom);
      int currentPirLiv = pirLiving.filteredState;
      int currentPirBed = pirBedroom.filteredState;
      int currentPirBath = pirBathroom.filteredState;
      int currentPirKit = pirKitchen.filteredState;
      
      
      
    // Gửi khẩn cấp
    if (doorLiving!= lastDoorLiv || currentPirLiv != lastPirLiv){
      lastDoorLiv = doorLiving;
      lastPirLiv = currentPirLiv;
      StaticJsonDocument<128>docLiving;
      docLiving["room"]="Livingroom";
      docLiving["door_status"]=doorLiving;
      docLiving["motion"]=currentPirLiv;
      docLiving["event"]="urgent";
      pushToQueue(topic_livingroom, docLiving);
    }
    if(doorBed!=lastDoorBed || currentPirBed!=lastPirBed){
      lastDoorBed = doorBed;
      lastPirBed = currentPirBed;
      StaticJsonDocument<256>docBed;
      docBed["room"]="Bedroom";
      docBed["door_status"]=doorBed;
      docBed["motion"]=currentPirBed;
      docBed["event"]="urgent";
      pushToQueue(topic_bedroom, docBed);
    }
    if(doorBath!=lastDoorBath || currentPirBath!=lastPirBath){
      lastDoorBath = doorBath;
      lastPirBath = currentPirBath;
      StaticJsonDocument<256>docBath;
      docBath["room"]="Bathroom";
      docBath["door_status"]=doorBath;
      docBath["motion"]=currentPirBath;
      docBath["event"]="urgent";
      pushToQueue(topic_bathroom, docBath);
    }
    if(currentPirKit!=lastPirKit){
      lastPirKit = currentPirKit;
      StaticJsonDocument<256>docKit;
      docKit["room"]="Kitchen";
      docKit["motion"]=currentPirKit;
      docKit["event"]="urgent";
      pushToQueue(topic_kitchen, docKit);
    }

    // gửi định kỳ
    if(now-previousMillis>interval){
      previousMillis=now;
      if (sgp.IAQmeasure()) {
      cache_tvoc = sgp.TVOC;
      cache_eco2 = sgp.eCO2;
    }
    // đọc giá trị cảm biến temp và humi
      float tempBed = dhtbedroom.readTemperature();
      float humiBed = dhtbedroom.readHumidity();
      if(!isnan(tempBed)&&!isnan(humiBed)){
        cache_tempBed=tempBed;
        cache_humiBed=humiBed;
      }
      float tempKit = dhtKitchen.readTemperature();
      float humiKit = dhtKitchen.readHumidity();
      if(!isnan(tempKit)&&!isnan(humiKit)){
        cache_tempKit=tempKit;
        cache_humiKit=humiKit;
      }
      float tempLiv=dhtlivingroom.readTemperature();
      float humiLiv=dhtlivingroom.readHumidity();
      if(!isnan(tempLiv)&&!isnan(humiLiv)){
        cache_tempLiv=tempLiv;
        cache_humiLiv=humiLiv;
      }
      // dọc cảm biến
      int gasVal=readAnalogAvg(GasPinKitchen);
      int RawLightVal=readAnalogAvg(LRDPIN);
      int lightVal=4095- RawLightVal;
      float lux = lightMeter.readLightLevel();
      // ====================================
      //          Gửi DaTa
      // ====================================

      // =========Đóng gói bed==============
      StaticJsonDocument<256>docBed;
      docBed["device_id"]=mqtt_id;
      docBed["room"]="Bedroom";
      docBed["temperature"]=cache_tempBed;
      docBed["humidity"]=cache_humiBed;
      docBed["light"]=lightVal;
      docBed["door_status"]=doorBed;
      docBed["motion"]=pirBedroom.filteredState;
      pushToQueue(topic_bedroom, docBed);

      // ============= Đóng gói kitchen===========
      StaticJsonDocument<256>docKit;
      docKit["device_id"]=mqtt_id;
      docKit["room"]="Kitchen";
      docKit["temperature"]=cache_tempKit;
      docKit["humidity"]=cache_humiKit;
      docKit["motion"]=pirKitchen.filteredState;
      docKit["gas"]=gasVal;
      pushToQueue(topic_kitchen, docKit);


    //=============== gói bathroom====================
      StaticJsonDocument<256>docBath;
      docBath["device_id"]=mqtt_id;
      docBath["room"]="Bathroom";
      docBath["motion"]=pirBathroom.filteredState;
      docBath["door_status"]=doorBath;
      pushToQueue(topic_bathroom, docBath);
    // =============== Gói livingroom================
      StaticJsonDocument<256>docLiving;
      docLiving["device_id"]=mqtt_id;
      docLiving["room"]="Livingroom";
      docLiving["temperature"]=tempLiv;
      docLiving["humidity"]=humiLiv;
      docLiving["motion"]=pirLiving.filteredState;
      docLiving["door_status"]=doorLiving;
      docLiving["tvoc"] = cache_tvoc;
      docLiving["eco2"] = cache_eco2;
      docLiving["light"]=lux;
      pushToQueue(topic_livingroom, docLiving);
      Serial.println("--- Đã xuất dữ liệu 4 phòng ---");
    }
    vTaskDelay(100 / portTICK_PERIOD_MS);
  }
}
void loop(){}
// // void loop() {
//   server.handleClient();
//   static unsigned long lastMqttAttempt=0;
//   if(!client.connected()){
//    if(millis()-lastMqttAttempt>5000){
//      lastMqttAttempt=millis();
//      connectMQTT();
//    }
//   }
//   client.loop();
//   pirBedroom.update();
//   pirBathroom.update();
//   pirKitchen.update();
//   pirLiving.update();
//   unsigned long now=millis();
//   unsigned long current=millis();
//   if (now - lastSGP30Millis > 1000) {
//    lastSGP30Millis = now;
//    if (sgp.IAQmeasure()) {
//      cache_tvoc = sgp.TVOC;
//      cache_eco2 = sgp.eCO2;
//    }
//   }
//   if(now-previousMillis>interval){
//    previousMillis=now;
//    // đọc giá trị cảm biến temp và humi
//    float tempBed = dhtbedroom.readTemperature();
//    float humiBed = dhtbedroom.readHumidity();
//    if(!isnan(tempBed)&&!isnan(humiBed)){
//      cache_tempBed=tempBed;
//      cache_humiBed=humiBed;
//    }
//    float tempKit = dhtKitchen.readTemperature();
//    float humiKit = dhtKitchen.readHumidity();
//    if(!isnan(tempKit)&&!isnan(humiKit)){
//      cache_tempKit=tempKit;
//      cache_humiKit=humiKit;
//    }
//    float tempLiv=dhtlivingroom.readTemperature();
//    float humiLiv=dhtlivingroom.readHumidity();
//    if(!isnan(tempLiv)&&!isnan(humiLiv)){
//      cache_tempLiv=tempLiv;
//      cache_humiLiv=humiLiv;
//    }
//    // dọc cảm biến
//    int gasVal=readAnalogAvg(GasPinKitchen);
//    int lightVal=readAnalogAvg(LRDPIN);
//    int doorBed=digitalRead(DoorSensorBedroom);
//    int doorBath=digitalRead(DoorSensorBathroom);
//    int doorLiving=digitalRead(DoorSensorLivingroom);
//    float lux = lightMeter.readLightLevel();
//    // ====================================
//    //          Gửi DaTa
//    // ====================================

//    // =========Đóng gói bed==============

//    StaticJsonDocument<256>docBed;
//    docBed["device_id"]=mqtt_id;
//    docBed["room"]="Bedroom";
//    docBed["temperature"]=cache_tempBed;
//    docBed["humidity"]=cache_humiBed;
//    docBed["light"]=lightVal;
//    docBed["door_status"]=doorBed;
//    docBed["motion"]=pirBedroom.filteredState;
//    char bufferBed[256];
//    serializeJson(docBed,bufferBed);
//    if(client.connected()){
//      client.publish(topic_bedroom,bufferBed);
//      Serial.println("Sent Bedroom"+ String(bufferBed));
//    }

//    // ============= Đóng gói kitchen===========
//    StaticJsonDocument<256>docKit;
//    docKit["device_id"]=mqtt_id;
//    docKit["room"]="Kitchen";
//    docKit["temperature"]=cache_tempKit;
//    docKit["humidity"]=cache_humiKit;
//    docKit["motion"]=pirKitchen.filteredState;
//    docKit["gas"]=gasVal;
//    char bufferKit[256];
//    serializeJson(docKit,bufferKit);
//    if(client.connected()){
//      client.publish(topic_kitchen,bufferKit);
//      Serial.println("Sent Kitchen"+ String(bufferKit));
//    }


// //=============== gói bathroom====================
//    StaticJsonDocument<256>docBath;
//    docBath["device_id"]=mqtt_id;
//    docBath["room"]="Bathroom";
//    docBath["motion"]=pirBathroom.filteredState;
//    docBath["door_status"]=doorBath;
//    char bufferBath[256];
//    serializeJson(docBath,bufferBath);
//    if(client.connected()){
//      client.publish(topic_bathroom,bufferBath);
//      Serial.println("Sent Bathroom"+ String(bufferBath));
//    }
// // =============== Gói livingroom================
//    StaticJsonDocument<256>docLiving;
//    docLiving["device_id"]=mqtt_id;
//    docLiving["room"]="Livingroom";
//    docLiving["temperature"]=tempLiv;
//    docLiving["humidity"]=humiLiv;
//    docLiving["motion"]=pirLiving.filteredState;
//    docLiving["door_status"]=doorLiving;
//    docLiving["tvoc"] = cache_tvoc;
//    docLiving["eco2"] = cache_eco2;
//    docLiving["light"]=lux;
//    char bufferliving[256];
//    serializeJson(docLiving,bufferliving);
//    if(client.connected()){
//      client.publish(topic_livingroom,bufferliving);
//      Serial.println("Sent livingroom"+ String(bufferliving));
//    }
//    Serial.println("--- Đã xuất dữ liệu 4 phòng ---");
//   }
// }

void startConfigPortal() {
  WiFi.mode(WIFI_AP);
  WiFi.softAP("ESP_Config_Sensor", "12345678");
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
  ssid = preferences.getString("ssid", "OngNgoai"); // Lấy WiFi cũ (nếu có)
  password = preferences.getString("password", "0913858753");
  preferences.end();
}

void saveWiFiConfig(String newSsid, String newPassword) {
  preferences.begin("wifi", false);
  preferences.putString("ssid", newSsid);
  preferences.putString("password", newPassword);
  preferences.end();
  Serial.println("Đã lưu WiFi mới: " + newSsid);
}
// kết nối wifi
void connectWiFi() {
  loadWiFiConfig();  
  
  if(ssid == "") {
   Serial.println("Chưa có WiFi lưu trữ. Bật chế độ cấu hình...");
   startConfigPortal();
  }

  Serial.println("Đang kết nối WiFi: " + ssid);
  WiFi.begin(ssid.c_str(), password.c_str());

  int retry = 0;
  while (WiFi.status() != WL_CONNECTED && retry < 20) {
   delay(500);
   Serial.print(".");
   retry++;
  }

  if (WiFi.status() == WL_CONNECTED) {
   Serial.println("\nWiFi " + ssid + " kết nối thành công!");
   Serial.println("MQTT server: " + String(mqtt_server));
   Serial.print("📡 IP ESP32: ");
   Serial.println(WiFi.localIP());
  } else {
   Serial.println("\nWiFi thất bại. Bật chế độ cấu hình...");
   startConfigPortal();
  }
}

void connectMQTT() {
  Serial.print("Connecting to MQTT...");
  if (client.connect(mqtt_id)) {
   Serial.println("connected");
   client.subscribe(topic_subscribe);
  } else {
   Serial.print("failed, rc=");
    Serial.println(client.state());
  }
}
