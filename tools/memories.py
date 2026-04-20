import os
import json 
import asyncpg
from dotenv import load_dotenv
from datetime import datetime

load_dotenv()
DB_POOL = None # Sẽ được main.py gán vào

# ========================= HELPER ==========================
def format_time(ts):
    return ts.strftime("%H:%M %d/%m/%Y") if ts else "unknown"

def normalize(text: str) -> str:
    return text.lower().strip()

# ===================== TOOLS & INTERNAL =====================
async def log_ai_decision(agent_name: str, room_id: int, context_data: dict, decision_data: dict, priority: int = 1):
    """ Hàm nội bộ: lưu lại quyết định của AI """
    if not DB_POOL:
        print("Lỗi: DB_POOL chưa sẵn sàng")
        return 
    async with DB_POOL.acquire() as conn:
        try:
            query = """
                INSERT INTO ai_decisions (agent_name, room_id, context, decision, priority, executed, executed_at)
                VALUES ($1, $2, $3::jsonb, $4::jsonb, $5, TRUE, NOW())
            """
            await conn.execute(
                query, agent_name, room_id,
                json.dumps(context_data), json.dumps(decision_data), priority
            )
            print(f" Đã ghi nhớ thành công hành động của {agent_name}")
        except Exception as e:
            print(f" Lỗi ghi nhớ: {str(e)}")


async def push_notification(title: str, message: str, severity: str = 'INFO', user_id: int = 1): # 🌟 ĐÃ FIX: tilte -> title
    """ TOOL CHO AI: Gửi thông báo cho người dùng """
    if not DB_POOL:
        return "Database chưa khởi tạo"
    async with DB_POOL.acquire() as conn:
        try:
            query = """
                INSERT INTO notifications (title, message, severity, user_id, created_at, is_read)
                VALUES ($1, $2, $3, $4, NOW(), FALSE)
            """
            await conn.execute(query, title, message, severity, user_id)
            print(f"[NOTIFY] {severity}: {title}")
            return "Đã gửi thông báo thành công."
        except Exception as e:
            print(f"Lỗi gửi thông báo: {str(e)}")
            return "Gặp lỗi khi gửi thông báo."


async def check_security_logs(minutes: int = 10) -> str:
    """ 
    Tra cứu lịch sử an ninh trong X phút vừa qua.
    Gemini sẽ tự động trích xuất X từ câu hỏi của user.
    """
    if DB_POOL is None:
        return "Lỗi: Database chưa khởi tạo"
    
    try:
        async with DB_POOL.acquire() as conn:
            
            query = """
                SELECT dl.created_at, dl.triggered_by, d.name, r.name as room_name, d.type::text
                FROM device_logs dl
                JOIN devices d ON dl.device_id = d.id
                LEFT JOIN rooms r ON d.room_id = r.id
                WHERE (d.type::text = 'door' OR d.type::text ILIKE '%sensor%')
                AND dl.created_at >= NOW() - INTERVAL '1 minute' * $1
                ORDER BY dl.created_at DESC
            """ 
            logs = await conn.fetch(query, minutes)
        
            if not logs:
                return f"Dạ anh, trong {minutes} phút qua em không thấy có biến động an ninh nào được ghi nhận ạ."
                
            result = []
            for log in logs:
                time_str = format_time(log['created_at'])
                user_str = log['triggered_by'] or 'Hệ thống'
                room_str = log['room_name'] or 'vị trí chung'
                
                # Logic phản hồi thông minh dựa trên loại thiết bị
                if "door" in log['type']:
                    result.append(f"- Lúc {time_str}, {user_str} đã mở {log['name']} tại {room_str}.")
                else:
                    result.append(f"- Lúc {time_str}, cảm biến phát hiện sự hiện diện của {user_str} tại {room_str}.")
                
            return f"Đây là lịch sử an ninh trong {minutes} phút gần nhất:\n" + "\n".join(result)
            
    except Exception as e:
        print(f"Lỗi check_security_logs: {e}")
        return f"Lỗi truy xuất lịch sử: {str(e)}"

async def check_device_history(device: str, location: str) -> str:
    """ Kiểm tra QUÁ KHỨ bật/tắt của 1 thiết bị """
    if DB_POOL is None:
        return "Lỗi: Database chưa khởi tạo"
    LOC_MAP = {
        "phòng khách": "livingroom", "khách": "livingroom",
        "phòng ngủ": "bedroom", "ngủ": "bedroom",
        "nhà bếp": "kitchen", "phòng bếp": "kitchen", "bếp": "kitchen",
        "nhà tắm": "bathroom", "phòng tắm": "bathroom", "tắm": "bathroom"
    }
    
    loc = normalize(location)
    loc_db = LOC_MAP.get(loc, loc) # Tên tiếng Anh
    loc_vi = loc.replace("phòng ", "").replace("nhà ", "").strip() # Từ khóa tiếng Việt
    
    try:
        r_filter_en = "%%" if loc == "all" else f"%{loc_en}%"
        r_filter_vi = "%%" if loc == "all" else f"%{loc_vi}%"
        s_filter_en = "%%" if st_lower == "all" else f"%{db_sensor_en}%"
        s_filter_vi = "%%" if st_lower == "all" else f"%{st_lower}%"
        async with DB_POOL.acquire() as conn:
            query = """
            SELECT dl.created_at, dl.action, dl.triggered_by, d.name
            FROM device_logs dl
            JOIN devices d ON dl.device_id = d.id
            LEFT JOIN rooms r ON d.room_id = r.id
            WHERE d.name ILIKE $1 AND (r.name ILIKE $2 OR r.name ILIKE $3)
            ORDER BY dl.created_at DESC 
            LIMIT 1
            """
            log = await conn.fetchrow(query, f"%{device}%", f"%{loc_db}%", f"%{loc_vi}%")
            
            if not log:
                return f"Anh ơi, em không thấy dữ liệu lịch sử nào về {device} ở {location} cả."
                
            action = "đã Bật/Mở" if log['action'] in ["ON", "OPEN"] else "đã Tắt/Đóng"
            time_str = format_time(log['created_at'])
            user_str = log['triggered_by'] or 'Hệ thống tự động'
            
            return f"Thiết bị '{log['name']}' tại {location} {action} lúc {time_str} bởi {user_str}."
            
    except Exception as e:
        print(f"Lỗi check_device_history: {e}")
        return f"Lỗi truy xuất lịch sử thiết bị: {str(e)}"
async def get_sensor_comparison(target_date: str, room_name: str = "all", sensor_type: str = "all") -> str:
    """ 
    Truy vấn thông số trung bình (nhiệt độ, độ ẩm, gas) của một ngày cụ thể trong quá khứ.
    """    
    if DB_POOL is None:
        return "Lỗi: Database chưa khởi tạo"
        
    print(f"\n[AI DEBUG] Bắt đầu tìm kiếm: Ngày={target_date}, Phòng='{room_name}', Cảm biến='{sensor_type}'")

    # 1. XỬ LÝ NGÀY THÁNG LINH HOẠT
    clean_date = target_date.replace('/', '-')
    date_obj = None
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d-%m", "%Y-%m-%d %H:%M:%S"):
        try:
            date_obj = datetime.strptime(clean_date, fmt).date()
            if date_obj.year == 1900: 
                date_obj = date_obj.replace(year=datetime.now().year)
            break
        except ValueError:
            continue
            
    if not date_obj:
        return f"Dạ, định dạng ngày '{target_date}' không đúng. Anh vui lòng dùng định dạng YYYY-MM-DD ạ."
    loc = room_name.lower().strip()
    LOC_MAP = {
        "phòng khách": "livingroom", "khách": "livingroom",
        "phòng ngủ": "bedroom", "ngủ": "bedroom",
        "nhà bếp": "kitchen", "phòng bếp": "kitchen", "bếp": "kitchen",
        "nhà tắm": "bathroom", "phòng tắm": "bathroom", "tắm": "bathroom"
    }
    loc_en = LOC_MAP.get(loc, loc)
    loc_vi = loc.replace("phòng ", "").replace("nhà ", "").strip()

    st_lower = sensor_type.lower().strip()
    SENSOR_MAP = {
        "nhiệt độ": "temperature", "nhiệt": "temperature", "temp": "temperature",
        "độ ẩm": "humidity", "ẩm": "humidity",
        "khí gas": "gas", "gas": "gas", "khí": "gas",
        "ánh sáng": "light", "sáng": "light"
    }
    db_sensor_en = SENSOR_MAP.get(st_lower, st_lower)

    try:
        # Tạo đa bộ lọc (Tìm cả tiếng Anh lẫn tiếng Việt để không lọt lưới)
        r_filter_en = "%%" if loc == "all" else f"%{loc_en}%"
        r_filter_vi = "%%" if loc == "all" else f"%{loc_vi}%"
        s_filter = "%%" if st_lower == "all" else f"%{db_sensor_en}%"
        s_filter_vi = "%%" if st_lower == "all" else f"%{st_lower}%"
        
        async with DB_POOL.acquire() as conn:
            # Lấy dữ liệu cũ
            query_old = """
                SELECT s.avg_value, sn.name as sensor_name, sm.metric_name
                FROM daily_sensor_stats s
                JOIN sensor_metrics sm ON s.metric_id = sm.id
                JOIN sensors sn ON sm.sensor_id = sn.id
                JOIN rooms r ON sn.room_id = r.id
                WHERE s.stat_date = $1::date
                AND (r.name ILIKE $2 OR r.name ILIKE $3)
                AND (sn.type::text ILIKE $4 OR sm.metric_name ILIKE $4 OR sm.metric_name ILIKE $5)
                LIMIT 1
            """
            old_data = await conn.fetchrow(query_old, date_obj, r_filter_en, r_filter_vi, s_filter, s_filter_vi)

            if not old_data:
                print("[AI DEBUG] ❌ Không tìm thấy dữ liệu CŨ trong DB! Chắc chắn do tên phòng/cảm biến lệch.")
                return f"Hệ thống: Không tìm thấy dữ liệu thống kê của '{sensor_type}' tại '{room_name}' vào ngày {date_obj.strftime('%d/%m/%Y')}."

            # Lấy dữ liệu mới nhất
            query_current = """
                SELECT sl.value 
                FROM sensor_logs sl
                JOIN sensor_metrics sm ON sm.id = sl.metric_id
                JOIN sensors sn ON sm.sensor_id = sn.id
                JOIN rooms r ON sn.room_id = r.id
                WHERE (r.name ILIKE $1 OR r.name ILIKE $2)
                AND (sn.type::text ILIKE $3 OR sm.metric_name ILIKE $3 OR sm.metric_name ILIKE $4)
                ORDER BY sl.created_at DESC LIMIT 1
            """
            current_data = await conn.fetchrow(query_current, r_filter_en, r_filter_vi, s_filter, f"%{st_lower}%")
            
            if not current_data:
                room_display = room_name if room_name != "all" else "toàn nhà"
                return f"Dạ, dữ liệu thống kê cho thấy {sensor_n} trung bình tại {room_display} vào ngày {date_obj.strftime('%d/%m/%Y')} là {round(old_data['avg_value'], 1)}{unit} ạ."
            
            diff = round(current_data['value'] - old_data['avg_value'], 1)
            
            metric = old_data['metric_name'].lower()
            sensor_n = old_data['sensor_name']
            
            if 'temp' in metric or 'nhiệt' in metric:
                status = "nóng hơn" if diff > 0 else "mát hơn"
                unit = "°C"
            elif 'humid' in metric or 'ẩm' in metric:
                status = "cao hơn" if diff > 0 else "thấp hơn"
                unit = "%"
            elif 'gas' in metric or 'khí' in metric:
                status = "đậm đặc hơn" if diff > 0 else "ít hơn"
                unit = "ppm"
            else:
                status = "tăng" if diff > 0 else "giảm"
                unit = "đơn vị"
            
            result_text = f"Dữ liệu: So với mức trung bình ({round(old_data['avg_value'],1)}{unit}) của ngày {date_obj.strftime('%d/%m')}, hôm nay {sensor_n} tại {room_name} đang {status} {abs(diff)}{unit}."
            print(f"[AI DEBUG] Kết quả trả về cho AI: {result_text}")
            return result_text
            
    except Exception as e:
        print(f"Lỗi get_sensor_comparison: {e}")
        return f"Lỗi truy xuất dữ liệu cảm biến: {str(e)}"
async def learn_preference(user_id: int, topic: str, details: str) -> str:
    """ 
    TOOL CHO AI: Dùng để HỌC và GHI NHỚ một sở thích, thói quen hoặc yêu cầu mới của chủ nhà.
    """
    if DB_POOL is None:
        return "Lỗi Database."
    try:
        async with DB_POOL.acquire() as conn:
            query = """
                INSERT INTO user_preferences (user_id, topic, details, updated_at) 
                VALUES ($1, $2, $3, NOW())
                ON CONFLICT (user_id, topic) 
                DO UPDATE SET details = EXCLUDED.details, updated_at = NOW()
            """
            await conn.execute(query, user_id, topic, details)
            return f"Đã ghi nhớ thành công vào dữ liệu: Chủ đề '{topic}' -> '{details}'."
    except Exception as e:
        print(f"Lỗi khi ghi nhớ: {str(e)}") # Bắn log ra màn hình đen để dễ debug
        return "Lỗi hệ thống khi ghi nhớ dữ liệu."


async def get_user_preferences(user_id: int = 1) -> str:
    """
    Hàm nội bộ: Lấy toàn bộ sở thích của user để nhét vào não AI.
    (Mặc định user_id = 1 là tài khoản của Huy, sau này bạn có thể truyền động vào từ Camera nhận diện)
    """
    if DB_POOL is None:
        return ""
    try:
        async with DB_POOL.acquire() as conn:
            rows = await conn.fetch("SELECT topic, details FROM user_preferences WHERE user_id = $1", user_id)
            if not rows:
                return "Chưa có dữ liệu sở thích nào được ghi nhớ."
            
            prefs = [f"- Về {r['topic']}: {r['details']}" for r in rows]
            return "THÔNG TIN SỞ THÍCH CÁ NHÂN:\n" + "\n".join(prefs)
    except Exception as e:
        print(f"Lỗi kéo dữ liệu sở thích: {str(e)}")
        return ""