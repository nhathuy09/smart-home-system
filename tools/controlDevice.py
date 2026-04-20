import os
import json
import asyncpg
from paho.mqtt import client as mqtt_client
from dotenv import load_dotenv
from tools.memories import log_ai_decision
import re
load_dotenv()

# --- Cấu hình MQTT ---
MQTT_BROKER = "14.225.224.167"
MQTT_PORT = 1881
MQTT_TOPIC_PUB = "smartHome/control/device"
MQTT_TOPIC_SUB = "smartHome/state/device"
# Bạn cần khởi tạo DB_POOL ở file main.py và gán vào biến này
DB_POOL = None 
client = mqtt_client.Client(callback_api_version=mqtt_client.CallbackAPIVersion.VERSION2)
def on_connect(client, userdata, flags, rc, properties=None):
    if rc == 0:
        print("Kết nối MQTT thành công")
        client.subscribe(MQTT_TOPIC_SUB)
    else:
        print(f"Lỗi kết nối MQTT: {rc}")
def on_message(client, userdata, msg):
    try:
        data = json.loads(msg.payload.decode())
        print(f"[MQTT Nhận]: {data}")
    except Exception as e:
        print(f"Lỗi nhận tin MQTT: {e}")

client.on_connect = on_connect
client.on_message = on_message
try:
    client.connect(MQTT_BROKER, MQTT_PORT)
    client.loop_start()
except Exception as e:
    print(f"Lỗi khởi tạo MQTT: {e}")
# --- Mapping vị trí chuẩn theo Database 'rooms' ---
LOCATION_MAP = {
    "phòng khách": "livingroom", "khách": "livingroom",
    "phòng ngủ": "bedroom", "ngủ": "bedroom",
    "nhà bếp": "kitchen", "phòng bếp": "kitchen", "bếp": "kitchen",
    "nhà tắm": "bathroom", "phòng tắm": "bathroom", "tắm": "bathroom",
    "cổng": "livingroom",
    "cửa chính": "livingroom",
    "cửa sổ phòng ngủ": "bedroom",
    "cửa sổ":"bedroom",
    "sổ":"bedroom",
    "mặc định": "livingroom"
}

# =========================================================
# HELPER FUNCTION BẤT ĐỒNG BỘ (ASYNC)
# =========================================================
async def find_device_in_db(device_vi: str, location_vi: str):
    if not DB_POOL: 
        print("Lỗi: DB_POOL chưa được khởi tạo!")
        return None
        
    async with DB_POOL.acquire() as conn:
        try:
            clean_dev = device_vi.lower().replace("bật", "").replace("tắt", "").replace("mở", "").replace("đóng", "").strip()
            clean_loc = location_vi.lower().replace("phòng", "").replace("nhà", "").strip()
            
            id_match = re.search(r'\d+', clean_dev)
            if id_match:
                device_id = int(id_match.group())
                return await conn.fetchrow("SELECT id, room_id, name, type, status FROM devices WHERE id = $1", device_id)
            query_all = """
                SELECT d.id, d.room_id, d.name, d.type, d.status 
                FROM devices d
                JOIN rooms r ON d.room_id = r.id
                WHERE d.name ILIKE $1 AND r.name ILIKE $2 
                LIMIT 1
            """
            res = await conn.fetchrow(query_all, f"%{clean_dev}%", f"%{clean_loc}%")
            if res: return res
            query_name_only = "SELECT id, room_id, name, type, status FROM devices WHERE name ILIKE $1 LIMIT 1"
            res_name = await conn.fetchrow(query_name_only, f"%{clean_dev}%")
            if res_name: return res_name
            return None
        except Exception as e:
            print(f"Lỗi truy vấn DB: {e}")
            return None

# =========================================================
# CÁC CÔNG CỤ (TOOLS) DÀNH CHO AI AGENT
# =========================================================

async def turn_on_device(device: str, location: str) -> str:
    dev_info = await find_device_in_db(device, location)
    print(f"DEBUG AI: Đang cố gắng bật {device} tại {location}. Kết quả DB: {dev_info}")
    if not dev_info:
        return f"Dạ, em tìm không thấy thiết bị '{device}' nào ở '{location}' để mở ạ."
    dev_id, room_id, db_name, dev_type, _ = dev_info
    if "chính" in device.lower() and dev_id != 6:
        return "Em tìm thấy cửa, nhưng ID không khớp với cửa chính. Anh hãy thử nói 'Mở cửa chính phòng khách' nhé."
    loc_en = LOCATION_MAP.get(location.lower(), "livingroom")
    payload = {
        "id": dev_id,
        "device": dev_type.lower(), 
        "location": loc_en,
        "status": "ON",
        "device_name": db_name.lower()
    }
    client.publish(MQTT_TOPIC_PUB, json.dumps(payload))
    
    await log_ai_decision(
        agent_name="Device_Agent",
        room_id=room_id, 
        context_data={"user_request": f"Yêu cầu bật {device} ở {location}"},
        decision_data={"action": "ON", "target": db_name, "success": True}
    )
    
    verb = "mở" if dev_type.lower() in ["door", "window","lock"] else "bật"
    return f"Đã xong! Em đã {verb} {db_name} ở {location} cho mình rồi nhé."


async def turn_off_device(device: str, location: str) -> str:
    dev_info = await find_device_in_db(device, location)
    if not dev_info:
        return f"Nhà mình hình như không có '{device}' ở '{location}' đâu ạ."
        
    dev_id, room_id, db_name, dev_type, _ = dev_info
    loc_en = LOCATION_MAP.get(location.lower(), "livingroom")

    payload = {
        "id": dev_id,
        "device": dev_type.lower(),
        "location": loc_en,
        "status": "OFF",
        "device_name": db_name.lower()
    }
    client.publish(MQTT_TOPIC_PUB, json.dumps(payload))
    
    await log_ai_decision(
        agent_name="Device_Agent",
        room_id=room_id,
        context_data={"user_request": f"Yêu cầu tắt {device} ở {location}"},
        decision_data={"action": "OFF", "target": db_name, "success": True}
    )
    
    verb = "đóng" if dev_type.lower() in ["door", "window","lock"] else "tắt"
    return f"Dạ, em đã {verb} {db_name} ở {location} xong rồi!"

async def check_status(device: str, location: str) -> str:
    dev_info = await find_device_in_db(device, location)
    if not dev_info: return f"Thiết bị '{device}' ở '{location}' không có trong hệ thống."
    dev_id, room_id, db_name, dev_type, current_status = dev_info
    is_on = (current_status.upper() == "ON")
    
    if dev_type.lower() in ["door", "window"]:
        state = "đang MỞ" if is_on else "đang ĐÓNG"
    else:
        state = "đang BẬT" if is_on else "đang TẮT"
        
    return f"Dạ, {db_name} ở {location} hiện {state} ạ."


async def bulk_control_devices(command: str, location: str = "tất cả") -> str:
    """
    Chỉ dùng khi người dùng yêu cầu điều khiển 'TẤT CẢ' hoặc 'CẢ NHÀ'.
    command: 'ON' hoặc 'OFF'.
    location: Tên phòng (VD: 'phòng khách') hoặc 'tất cả'.
    """
    if not DB_POOL: return "Lỗi kết nối dữ liệu."
    
    async with DB_POOL.acquire() as conn:
        try:
            if location.lower() in ["tất cả", "cả nhà", "all", ""]:
                devices = await conn.fetch("SELECT id,room_id, name, type FROM devices")
            else:
                loc_keyword = location.replace("phòng ", "").replace("nhà ", "").strip()
                devices = await conn.fetch("SELECT id,room_id, name, type FROM devices WHERE name ILIKE $1", f"%{loc_keyword}%")
            
            if not devices: return f"Không có thiết bị nào tại {location} để điều khiển."

            for d_id, room_id, d_name, d_type in devices:
                l_en = "livingroom"
                for k, v in LOCATION_MAP.items():
                    if k in d_name.lower(): 
                        l_en = v
                        break
                    
                payload = {"id": d_id, "device": d_type.lower(), "location": l_en, "status": command.upper()}
                client.publish(MQTT_TOPIC_PUB, json.dumps(payload))
                
            return f"OK Huy! Em đã thực hiện {command} cho toàn bộ thiết bị tại {location}."
        except Exception as e:
            print(f"Lỗi bulk_control: {e}")
            return "Đã xảy ra lỗi khi điều khiển hàng loạt."

async def schedule_task(task_type:str,action:str,target:str,location:str,delay_minutes:int):
    """
    TOOL CHO AI: Dùng để hẹn giờ thực hiện một tác vụ trong tương lai.
    - task_type: "DEVICE_CONTROL"
    - action: "ON" hoặc "OFF"
    - target: Tên thiết bị (VD: "quạt", "đèn")
    - location: Nơi chốn (VD: "phòng ngủ")
    - delay_minutes: Số phút đếm ngược cho đến khi thực hiện (VD: 30)
    """
    if not DB_POOL:
        return "Lỗi hệ thống mất dữ liệu kết nối"
    payload={
        "action":action.upper(),
        "target":target.lower(),
        "location":location.lower(),
        "delay_minutes":delay_minutes
    }
    async with DB_POOL.acquire() as conn:
        try:
            query = """
                INSERT INTO agent_tasks (agent_name, task_type, payload, status, updated_at)
                VALUES ($1, $2, $3::jsonb, 'PENDING', NOW())
            """
            await conn.execute(query,"Device_Agent",task_type,json.dumps(payload))
            return f"Đã lên lịch {action} {target} tại {location} sau {delay_minutes} phút."
        except Exception as e:
            print(f"Lỗi lên lịch: {str(e)}")
            return "Đã xảy ra lỗi khi lên lịch."