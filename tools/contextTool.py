import json
import asyncpg
from datetime import datetime
from zoneinfo import ZoneInfo
# DB_POOL sẽ được main.py tự động bơm vào lúc khởi động server
DB_POOL = None

# ===================== HELPER =====================
def normalize(text: str) -> str:
    return text.lower().strip()

# ==================== MAIN TOOL ====================
async def get_room_snapshot(location: str) -> str:
    """
    Sử dụng công cụ này để CHỤP LẠI TOÀN CẢNH (Snapshot) của một căn phòng.
    Nó sẽ trả về danh sách các thiết bị đang bật/tắt và các chỉ số môi trường mới nhất.
    """
    if DB_POOL is None:
        return "Lỗi: Database chưa được khởi tạo."
    LOC_MAP = {
        "phòng khách": "livingroom", "khách": "livingroom",
        "phòng ngủ": "bedroom", "ngủ": "bedroom",
        "nhà bếp": "kitchen", "phòng bếp": "kitchen", "bếp": "kitchen",
        "nhà tắm": "bathroom", "phòng tắm": "bathroom", "tắm": "bathroom"
    }
    
    loc = normalize(location)
    loc_en = LOC_MAP.get(loc, loc) # Lấy từ khóa tiếng Anh (VD: 'bedroom')
    loc_vi = loc.replace("phòng ", "").replace("nhà ", "").strip() # Lấy từ khóa tiếng Việt (VD: 'ngủ')
        
    try:
        async with DB_POOL.acquire() as conn:
            
            # 1. Lấy trạng thái thiết bị (Tìm theo cả Anh lẫn Việt)
            query_devices = """
            SELECT d.name, d.type, d.status 
            FROM devices d
            JOIN rooms r ON d.room_id = r.id
            WHERE (r.name ILIKE $1 OR r.name ILIKE $2)
            """
            devices = await conn.fetch(query_devices, f"%{loc_en}%", f"%{loc_vi}%")
            
            # 2. Lấy dữ liệu cảm biến MỚI NHẤT (Tìm theo cả Anh lẫn Việt)
            query_sensors = """
            SELECT DISTINCT ON (sm.metric_name) sm.metric_name, sl.value, sm.unit
            FROM sensor_logs sl
            JOIN sensor_metrics sm ON sl.metric_id = sm.id
            JOIN sensors s ON sm.sensor_id = s.id
            JOIN rooms r ON s.room_id = r.id
            WHERE (r.name ILIKE $1 OR r.name ILIKE $2)
            ORDER BY sm.metric_name, sl.created_at DESC
            """
            sensors = await conn.fetch(query_sensors, f"%{loc_en}%", f"%{loc_vi}%")

            if not devices and not sensors:
                return f"Dạ, em không tìm thấy thiết bị hay cảm biến nào ở {location} trong hệ thống ạ."

            # =============== FORMAT DỮ LIỆU CHUẨN ===============
            sensor_data = {sen['metric_name'].lower(): float(sen['value']) for sen in sensors}
            
            device_list = [f"- {dev['name']}: Đang {dev['status']}" for dev in devices]
            sensor_list = [f"- {sen['metric_name']}: {sen['value']} {sen['unit']}" for sen in sensors]
            
            # =============== LOGIC QUÂN SƯ ===============
            insights = []
            
            temp = sensor_data.get("temperature")
            light = sensor_data.get("light_level") # Cập nhật đúng tên từ DB
            gas = sensor_data.get("gas")
            eco2 = sensor_data.get("eco2")
            motion = sensor_data.get("motion")
            
            is_fan_on = any(d["type"] == "fan" and d["status"] == "ON" for d in devices)
            is_light_on = any(d["type"] == "light" and d["status"] == "ON" for d in devices)
            
            if temp is not None:
                if temp > 31 and not is_fan_on:
                    insights.append("Phòng đang NÓNG nhưng quạt chưa bật -> KHUYÊN BẬT QUẠT.")
                elif temp < 25 and is_fan_on:
                    insights.append("Phòng đang MÁT mà quạt vẫn bật -> KHUYÊN TẮT QUẠT.")
            
            if light is not None:
                if light < 50 and not is_light_on and motion == 1:
                    insights.append("Phòng TỐI và đang CÓ NGƯỜI -> KHUYÊN BẬT ĐÈN.")
                elif light > 300 and is_light_on:
                    insights.append("Trời ĐANG SÁNG nhưng đèn vẫn bật -> KHUYÊN TẮT ĐÈN để tiết kiệm điện.")
                    
            if gas is not None and gas > 800:
                insights.append("CẢNH BÁO: Khí Gas cao -> KHUYÊN BẬT QUẠT HÚT NGAY.")
                
            if motion is not None and motion == 0:
                if is_fan_on or is_light_on:
                    insights.append("Phòng KHÔNG CÓ NGƯỜI nhưng thiết bị vẫn bật -> KHUYÊN TẮT HẾT để tiết kiệm.")
            vn_tz = ZoneInfo('Asia/Ho_Chi_Minh')
            current_time = datetime.now(vn_tz).strftime("%H:%M:%S")
            # =============== ĐÓNG GÓI CHO LLM ===============
            result = {
                "room": location,
                "time": current_time,
                "devices": device_list if device_list else ["Không có thiết bị"],
                "sensors": sensor_list if sensor_list else ["Không có cảm biến"],
                "hints_for_ai": insights if insights else ["Mọi thứ đang ở trạng thái lý tưởng."]
            }
            
            return json.dumps(result, ensure_ascii=False, indent=2)
            
    except Exception as e:
        print(f"❌ Lỗi Context Snapshot: {e}")
        return f"Lỗi khi đọc dữ liệu ngữ cảnh: {str(e)}"