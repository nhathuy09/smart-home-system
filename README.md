# 🏠 Smart Home AI Pro Max - Multi-Agent & Computer Vision System

Hệ thống nhà thông minh thế hệ mới tích hợp Trí tuệ nhân tạo Đa đặc vụ (Multi-Agent AI) và Thị giác máy tính (Computer Vision) để nhận diện khuôn mặt chống giả mạo.

## 🚀 Điểm nhấn Công nghệ
- **Multi-Agent AI:** Sử dụng Google Gemini (ADK) để định tuyến tác vụ thông minh giữa các Agent (Thiết bị, Cảm biến, Ngữ cảnh).
- **AI Vision:** Nhận diện khuôn mặt bằng InsightFace kết hợp thuật toán MiDaS (Depth Estimation) để chống tấn công giả mạo (Anti-spoofing).
- **Kiến trúc Phân tán:** Hệ thống gồm 3 Node Edge (2x ESP32, 1x ESP32-S3-CAM) xử lý song song, tối ưu hóa băng thông và độ trễ.
- **Dynamic Config:** Khả năng cấu hình chân GPIO và loại thiết bị động qua OTA/SPIFFS mà không cần nạp lại mã nguồn.

## 🏗️ Kiến trúc Hệ thống
Hệ thống được chia thành 4 tầng xử lý chính:
1. **Edge Layer (Hardware):** 2 ESP32 (14 cảm biến, 8 relay) + ESP32-S3-CAM.
2. **Communication Layer:** MQTT Mosquitto (Real-time), WebSocket Secure (Video Stream).
3. **Brain Layer (Backend):** FastAPI Python xử lý Logic & AI Agent.
4. **Data Layer:** PostgreSQL với `pgvector` để lưu trữ và so khớp khuôn mặt siêu chiều.

## 🛠️ Danh sách Linh kiện (Hardware)
- **MCU:** 2x ESP32 DevKit V1, 1x ESP32-S3-CAM.
- **Cảm biến (14 loại):** DHT11 (Nhiệt/Ẩm), MQ-2 (Gas), BH1750 (Ánh sáng), PIR (Chuyển động), Cửa từ...
- **Chấp hành:** Module 8 Relay, Động cơ Servo (Khóa cửa).

## 💻 Cài đặt Phần mềm

### 1. Yêu cầu hệ thống
- Python 3.10+
- PostgreSQL (có cài extension `pgvector`)
- Mosquitto MQTT Broker

### 2. Triển khai Backend
```bash
# Clone dự án
git clone [https://github.com/vonhathuy/smart-home-system.git](https://github.com/vonhathuy/smart-home-system.git)
cd smart-home-system

# Cài đặt thư viện
pip install -r requirements.txt

# Cấu hình biến môi trường
cp .env.example .env
# Chỉnh sửa file .env với API Key và DB của bạn

# Chạy Server
uvicorn main:app --reload