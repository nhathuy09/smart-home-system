import os
import asyncpg
from dotenv import load_dotenv

load_dotenv()
DB_POOL = None 

# ===================== HELPER ==================================
def normalize(text: str) -> str: 
    return text.lower().strip()

# ==================== MAIN TOOL ===============================
async def get_environment_data(metric: str, location: str) -> str:
    """
    Sử dụng công cụ này để KIỂM TRA MÔI TRƯỜNG: ĐỘ SÁNG, KHÔNG KHÍ, NHIỆT ĐỘ, ĐỘ ẨM hoặc XEM CÓ NGƯỜI (chuyển động).
    Dữ liệu trả về mang tính đánh giá trạng thái tự nhiên.
    """
    if DB_POOL is None:
        return "Lỗi: Database chưa sẵn sàng"
        
    # 1. Mapping ngôn ngữ tự nhiên sang Key của Database
    LOC_MAP = {
        "phòng khách": "livingroom", "khách": "livingroom",
        "phòng ngủ": "bedroom", "ngủ": "bedroom",
        "nhà bếp": "kitchen", "phòng bếp": "kitchen", "bếp": "kitchen",
        "nhà tắm": "bathroom", "phòng tắm": "bathroom", "tắm": "bathroom"
    }
    METRIC_MAP = {
        "nhiệt độ": ["temperature"], 
        "độ ẩm": ["humidity"],       
        "ánh sáng": ["light_level"], "sáng": ["light_level"], "tối": ["light_level"], 
        "khí gas": ["gas"], "gas": ["gas"], 
        "không khí": ["eco2", "tvoc", "gas"], 
        "chất lượng không khí": ["eco2", "tvoc", "gas"], 
        "ngột ngạt": ["eco2", "tvoc", "gas"],
        "chuyển động": ["motion"], "người": ["motion"], "ai": ["motion"],
        "cửa": ["door_status"], "cửa sổ": ["door_status"]
    }
    
    loc = normalize(location)
    metric = normalize(metric)
    
    loc_en = LOC_MAP.get(loc, loc) # Lấy tiếng Anh (VD: 'bedroom')
    loc_vi = loc.replace("phòng ", "").replace("nhà ", "").strip() # Lấy từ khóa Việt (VD: 'ngủ')
    
    metric_db = [metric]
    for k, v in METRIC_MAP.items():
        if k in metric:
            metric_db = v
            break
            
    try:
        async with DB_POOL.acquire() as conn:   
            query = """
            SELECT sl.value, sm.unit, sm.metric_name
            FROM sensor_logs sl
            JOIN sensor_metrics sm ON sl.metric_id = sm.id
            JOIN sensors s ON sm.sensor_id = s.id
            JOIN rooms r ON s.room_id = r.id
            WHERE (r.name ILIKE $1 OR r.name ILIKE $2) 
              AND sm.metric_name = ANY($3)
            ORDER BY sl.created_at DESC
            LIMIT 1
            """
            result = await conn.fetchrow(query, f"%{loc_en}%", f"%{loc_vi}%", metric_db)
            
            if not result:
                return f"Dạ anh, em chưa thấy dữ liệu về {metric} tại {location} trên hệ thống ạ."
            
            val_float = float(result['value'])
            unit = result['unit'] or ""
            metric_name = result['metric_name'].lower()
            
            # =============== ĐÁNH GIÁ NGỮ NGHĨA ===============
            # Cảm biến chuyển động
            if "motion" in metric_name:
                return f"Dạ, em thấy ĐANG CÓ NGƯỜI ở {location} ạ." if val_float > 0 else f"Dạ, hiện tại {location} KHÔNG CÓ AI cả ạ."
            
            # Cảm biến ánh sáng
            if "light" in metric_name:
                if val_float <= 50: return f"{location} rất tối ({val_float}{unit})"
                elif val_float <= 300: return f"{location} hơi tối ({val_float}{unit})"
                elif val_float <= 800: return f"{location} ánh sáng ổn ({val_float}{unit})"
                else: return f"{location} rất sáng ({val_float}{unit})"

            # Cảm biến không khí (eco2/tvoc/gas)
            if metric_name in ["eco2", "tvoc", "gas"]:
                if val_float >= 1000: 
                    if "bếp" in loc_vi:
                        return f"Không khí {location} đang rất bí bách ({val_float}{unit}), anh nên bật quạt hút mùi ngay nhé."
                    else:
                        return f"Không khí {location} đang rất bí bách ({val_float}{unit}), anh nên mở cửa để thông gió nhé."
                if val_float >= 600: return f"Không khí {location} hơi ngột ngạt một chút ({val_float}{unit})."
                return f"Chất lượng không khí tại {location} rất trong lành ({val_float}{unit}) ạ."

            # Nhiệt độ
            if "temp" in metric_name:
                if val_float >= 31: return f"{location} rất nóng ({val_float}{unit})"
                elif val_float >= 27: return f"{location} hơi nóng ({val_float}{unit})"
                elif val_float >= 22: return f"{location} rất mát ({val_float}{unit})"
                else: return f"{location} rất lạnh ({val_float}{unit})"
                
            # Độ ẩm
            if "humi" in metric_name:
                if val_float >= 70: return f"{location} rất ẩm ({val_float}{unit})"
                elif val_float >= 60: return f"{location} hơi ẩm ({val_float}{unit})"
                elif val_float >= 40: return f"{location} dễ chịu ({val_float}{unit})"
                else: return f"{location} rất khô ({val_float}{unit})"
                
            # Dự phòng nếu không lọt vào if nào
            return f"Dạ, {metric} tại {location} đang là {round(val_float, 1)}{unit} ạ."
            
    except Exception as e:
        print(f"Lỗi SensorTool: {e}")
        return f"Dạ, em gặp lỗi khi đọc cảm biến: {str(e)}"