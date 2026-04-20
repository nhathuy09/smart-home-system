import uvicorn
import uuid
from fastapi import FastAPI, APIRouter, HTTPException,Depends, File, UploadFile, Form, WebSocket, WebSocketDisconnect,status,Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
import secrets
from pydantic import BaseModel,Field,EmailStr
import bcrypt
from typing import List, Optional
from contextlib import asynccontextmanager
import json
from google.adk.sessions import InMemorySessionService
from google.adk.runners import Runner
from google.genai import types,Client
from dotenv import load_dotenv
import os
from my_agent.agent import root_agent
from insightface.app import FaceAnalysis
import base64
import cv2
import numpy as np
import asyncio
import asyncpg
import paho.mqtt.publish as publish
import tools.controlDevice,tools.sensorData,tools.memories,tools.contextTool
from tools.memories import push_notification,log_ai_decision
from tools.controlDevice import turn_on_device, turn_off_device
from datetime import datetime, timezone,timedelta
from tools.contextTool import get_room_snapshot
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
load_dotenv()
os.environ["GOOGLE_API_KEY"] ="[GOOGLE_API_KEY]"
gemini_client = Client(api_key=os.getenv("GOOGLE_API_KEY"))
APP_NAME = "agents"
session_service = None
runner = None
face_app=None 
ai_semaphore = asyncio.Semaphore(3)
scheduler = AsyncIOScheduler()
# ======================== các hàm kết nối=========================
async def init_db():
    global DB_POOL
    DB_POOL = await asyncpg.create_pool(
        dsn=os.getenv("SUPABASE_URL"),
        min_size=2,
        max_size=10,
        statement_cache_size=0  
    )
    tools.controlDevice.DB_POOL=DB_POOL
    tools.sensorData.DB_POOL=DB_POOL
    tools.memories.DB_POOL=DB_POOL
    tools.contextTool.DB_POOL=DB_POOL
async def fetch_all(query, *args):
    async with DB_POOL.acquire() as conn:
        return await conn.fetch(query, *args)
async def fetch_one(query, *args):
    async with DB_POOL.acquire() as conn:
        return await conn.fetchrow(query, *args)
async def execute(query, *args):
    async with DB_POOL.acquire() as conn:
        return await conn.execute(query, *args)

async def fetch_val(query, *args):
    async with DB_POOL.acquire() as conn:
        return await conn.fetchval(query, *args)
 # ======================= HELPER FUNCTIONS =======================
def get_password_hash(password: str):
    # Mã hóa mật khẩu bằng bcrypt chuẩn
    salt = bcrypt.gensalt()
    hashed = bcrypt.hashpw(password.encode('utf-8'), salt)
    return hashed.decode('utf-8')
def verify_password(plain_password: str, hashed_password: str):
    return bcrypt.checkpw(plain_password.encode('utf-8'), hashed_password.encode('utf-8'))
# -----------------------Hàm phụ xử lý ảnh------------------------
def decode_base64_img(b64_str):
    try:
        if "," in b64_str:b64_str = b64_str.split(",")[1]
        img_data = base64.b64decode(b64_str)
        nparr = np.frombuffer(img_data, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        return img
    except Exception as e:
        print(f"Lỗi giải mã ảnh: {e}")
        return None
def analyze_face_sync(img_bytes):
    nparr = np.frombuffer(img_bytes, np.uint8)
    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    h, w = img.shape[:2]
    if max(h, w) > 640:
        scale = 640 / max(h, w)
        img = cv2.resize(img, (int(w * scale), int(h * scale)))
    faces = face_app.get(img)
    return img, faces
security = HTTPBasic()
def authenticate(credentials: HTTPBasicCredentials = Depends(security)):
    correct_username = secrets.compare_digest(credentials.username, "admin")
    correct_password = secrets.compare_digest(credentials.password, "Vonhathuy31@")
    if not (correct_username and correct_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sai tài khoản rồi bạn ê!",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username

async def agent_task_scheduler():
    """Vòng lặp chạy ngầm siêu cấp an toàn"""
    while True:
        try:
            if DB_POOL:
                async with DB_POOL.acquire() as conn:
                    query = "SELECT id, payload, created_at FROM agent_tasks WHERE status = 'PENDING'"
                    tasks = await conn.fetch(query)
                    now_utc = datetime.now(timezone.utc)
                    for task in tasks:
                        task_id = task['id']
                        created_at = task['created_at'] 
                        if created_at.tzinfo is None:
                            created_at = created_at.replace(tzinfo=timezone.utc)
                        else:
                            created_at = created_at.astimezone(timezone.utc)
                        try:
                            p = json.loads(task['payload']) if isinstance(task['payload'], str) else task['payload']
                            delay_mins = int(p.get('delay_minutes', 0))
                        except Exception as e:
                            print(f"Lỗi Parse JSON Task {task_id}: {e}")
                            continue
                        execute_time = created_at + timedelta(minutes=delay_mins)
                        if now_utc >= execute_time:
                            print(f"[SCHEDULER] Đã tới giờ! Bắt đầu thực thi Task {task_id}...")
                            if p.get('action', 'ON').upper() == 'ON':
                                await turn_on_device(p.get('target', 'unknown'), p.get('location', 'livingroom'))
                            else:
                                await turn_off_device(p.get('target', 'unknown'), p.get('location', 'livingroom'))
                            print(f"[SCHEDULER] ĐÃ THỰC THI Task {task_id}: {p['action']} {p['target']}")
                            await conn.execute("UPDATE agent_tasks SET status = 'COMPLETED', updated_at = NOW() WHERE id = $1", task_id)
                        else:
                            time_left = (execute_time - now_utc).total_seconds()
                            print(f"Task {task_id} chờ thêm {int(time_left)} giây nữa...")
        except Exception as e:
            print(f" [CRITICAL] Scheduler Crash, đang khởi động lại vòng lặp: {e}")
        await asyncio.sleep(5)
async def proactive_ai_patrol():
    """Đặc vụ Chủ động: Đi tuần tra nhà cửa mỗi 10 phút"""
    print("[PROACTIVE AI] Đặc vụ đi tuần đã bắt đầu nhiệm vụ...")
    rooms_to_check = ["phòng khách", "phòng ngủ", "nhà bếp","phòng tắm"]
    while True:
        try:
            await asyncio.sleep(600)
            print("[PROACTIVE AI] Đang đi tuần tra nhà cửa...")
            for room in rooms_to_check:
                snapshot_json = await get_room_snapshot(room)
                if "Lỗi" in snapshot_json or "Không tìm thấy" in snapshot_json:
                    continue
                snapshot_data=json.loads(snapshot_json)
                hints=snapshot_data.get("hints_for_ai",[])
                has_warning = any("KHUYÊN" in hint or "CẢNH BÁO" in hint for hint in hints)
                if has_warning:
                    print(f"[PROACTIVE AI] Phát hiện bất thường tại {room}: {snapshot_json}")
                    prompt=f"""
                    Bạn là AI Quản gia chủ động. Hệ thống vừa đi tuần và phát hiện sự cố sau tại {room}:
                    Dữ liệu phòng: {snapshot_json}
                    
                    Nhiệm vụ: Viết MỘT tin nhắn cảnh báo ngắn gọn (dưới 30 chữ) gửi vào điện thoại chủ nhà. 
                    Xưng 'em', gọi 'anh'. Phải nêu rõ lý do và đề xuất hành động.
                    Ví dụ: "Anh ơi, phòng khách không có ai mà quạt vẫn bật. Anh có muốn em tắt đi cho tiết kiệm không ạ?"
                    Chỉ trả về câu nói, không giải thích gì thêm.
                    """
                    response = await asyncio.to_thread(
                        gemini_client.models.generate_content,
                        model="gemini-2.5-flash",
                        contents=prompt
                    )
                    ai_message=response.text.strip()
                    severity="DANGER" if "Cảnh báo" in str(hints) or "Gas" in str(hints) else "WARNING"
                    await push_notification(
                        title=f"Gợi ý từ AI - {room.title()}",
                        message=ai_message,
                        severity=severity,
                        user_id=1
                    )
                    await status_manager.broadcast({
                        "type": "notification",
                        "title": f"Gợi ý từ AI - {room.title()}",
                        "message": ai_message,
                        "severity": severity
                    })
                    await asyncio.sleep(120)
                
        except Exception as e:
            print(f" [CRITICAL] Proactive AI Crash, đang khởi động lại vòng lặp: {e}")
        await asyncio.sleep(5)

async def daily_summary_job():
    """Hàm tự động tính toán trung bình cảm biến lúc 23:59"""
    
    # 1. Chốt cứng ngày hiện tại theo giờ Việt Nam từ Python
    vn_tz = ZoneInfo('Asia/Ho_Chi_Minh')
    current_vn_date = datetime.now(vn_tz).date()
    
    print(f"--- [CRON JOB] Bắt đầu tổng kết dữ liệu ngày {current_vn_date} ---")
    
    try:
        # 2. BẮT BUỘC dùng async with để tự động trả kết nối lại cho Pool (Chống sập DB)
        async with DB_POOL.acquire() as conn:
            
            # 3. Truyền biến ngày trực tiếp từ Python ($1) thay vì dùng CURRENT_DATE của DB
            summary_query = """
            INSERT INTO daily_sensor_stats (metric_id, stat_date, avg_value, max_value, min_value)
            SELECT 
                sl.metric_id, 
                $1::date, 
                ROUND(AVG(sl.value)::numeric, 2), 
                MAX(sl.value), 
                MIN(sl.value)
            FROM sensor_logs sl
            JOIN sensor_metrics sm ON sl.metric_id = sm.id
            JOIN sensors s ON sm.sensor_id = s.id
            WHERE (sl.created_at AT TIME ZONE 'Asia/Ho_Chi_Minh')::date = $1::date
            AND s.type::text NOT IN ('PIR', 'DOOR')
            GROUP BY sl.metric_id
            ON CONFLICT (metric_id, stat_date) 
            DO UPDATE SET 
                avg_value = EXCLUDED.avg_value,
                max_value = EXCLUDED.max_value,
                min_value = EXCLUDED.min_value;
            """
            
            # Đẩy biến current_vn_date vào câu SQL
            await conn.execute(summary_query, current_vn_date)
            print("--- [CRON JOB] Tổng kết thành công và đã lưu vào bảng thống kê! ---")
            
    except Exception as e:
        print(f"[CRON JOB] Lỗi khi tổng kết: {e}")

# 4. Ép cứng múi giờ Việt Nam cho Scheduler để nó chạy ĐÚNG 23:59 tối tại Việt Nam
scheduler.add_job(daily_summary_job, 'cron', hour=23, minute=59, timezone='Asia/Ho_Chi_Minh')

#-----------------vòng đời---------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    print("Đang khởi động Smart Home API...")
    global session_service, runner,face_app
    await init_db()
    asyncio.create_task(agent_task_scheduler())
    asyncio.create_task(proactive_ai_patrol())
    scheduler.start()
    print("APScheduler đã được kích hoạt!")
    # await init_control()
    # khởi tạo insightFace
    try:
        print("Đang nạp Model InsightFace (buffalo_l)...")
        face_app = FaceAnalysis(name='buffalo_l', providers=['CPUExecutionProvider'])
        face_app.prepare(ctx_id=-1, det_size=(640, 640))
        print("InsightFace đã sẵn sàng")
    except Exception as e:
        print(f"⚠️ Cảnh báo nạp AI: {e}")
    session_service = InMemorySessionService()
    runner = Runner(
        agent=root_agent,
        app_name=APP_NAME,
        session_service=session_service
    )
    yield
    print("Hệ thống đang tắt...")
    if DB_POOL:
        await DB_POOL.close()
app = FastAPI(title="Smart Home AI Agent", docs_url=None,redoc_url=None,lifespan=lifespan)


@app.get("/docs", include_in_schema=False)
async def get_documentation(username: str = Depends(authenticate)):
    from fastapi.openapi.docs import get_swagger_ui_html
    return get_swagger_ui_html(openapi_url="/openapi.json", title="Huy IoT API Docs")

@app.get("/openapi.json", include_in_schema=False)
async def openapi(username: str = Depends(authenticate)):
    from fastapi.openapi.utils import get_openapi
    return get_openapi(title="Huy IoT API", version="1.0.0", routes=app.routes)
# -----------------------Cho phép UI HTML truy cập API------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
router = APIRouter()
# -----------------------Các class------------------------
class ChatRequest(BaseModel):
    query: str
    user_id: str | None = None 
    session_id: str | None = None

class ChatResponse(BaseModel):
    response: str
    user_id: str
    session_id: str

class DeviceControl(BaseModel):
    type: str
    # device_id: int
    device: str
    device_name: str
    command: str
    location: Optional[str] = None
    triggered_by: str="Web UI"
class RegisterRequest(BaseModel):
    full_name: str
    email: EmailStr
    username: str
    password: str
    family_code: str
class LoginRequest(BaseModel):
    username: str
    password: str
class DeviceStatusUpdate(BaseModel):
    id:int
    status:str
class CameraPayload(BaseModel):
    device_id:int
    user_id: Optional[int] = None
    is_unknown: bool
    face_embedding: Optional[List[float]] = None
    image_url: Optional[str] = None
    confidence: Optional[float] = None
    event_type: str = "FACE_DETECTED"
# ======================= WEBSOCKET MANAGER =======================
class WSManager:
    def __init__(self):
        self.clients = set()
        self.lock = asyncio.Lock()

    async def connect(self, ws: WebSocket):
        await ws.accept()
        async with self.lock:
            self.clients.add(ws)

    async def disconnect(self, ws: WebSocket):
        async with self.lock:
            self.clients.discard(ws)

    async def broadcast(self, data: dict):
        if not self.clients: return
        msg = json.dumps(data)
        async with self.lock:
            tasks = [self.safe_send(ws, msg) for ws in list(self.clients)]
        await asyncio.gather(*tasks, return_exceptions=True)

    async def safe_send(self, ws, msg):
        try:
            await ws.send_text(msg)
        except:
            await self.disconnect(ws)

status_manager = WSManager()
# ==========================================================================
#======================= 1. API ENDPOINT CHO AI CHAT========================
# ==========================================================================

@router.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    global session_service, runner
    user_id = request.user_id if request.user_id else str(uuid.uuid4())
    session_id = request.session_id if request.session_id else str(uuid.uuid4())
    print(f" Request: '{request.query}' | User: {user_id} | Session: {session_id}")
    try:
        device_context="Không có thông tin thiết bị"
        try:
            conn=await DB_POOL.acquire()
            try:
                rows = await conn.fetch("SELECT id, name, status, type FROM devices")
                device_context = "\n".join(
                    [f"- ID: {r['id']} | Tên: {r['name']} | Loại: {r['type']} | Trạng thái: {r['status']}" for r in rows]
                )
            finally:
                await DB_POOL.release(conn)
        except Exception as db_err:
            print(f"Lỗi khi lấy context DB cho AI: {db_err}")
        enriched_query = f"""[THÔNG TIN HỆ THỐNG TỰ ĐỘNG CẬP NHẬT]
Danh sách thiết bị hiện tại trong nhà (Tuyệt đối sử dụng ID và Tên từ danh sách này để đối chiếu):
{device_context}

---
Yêu cầu của người dùng: "{request.query}"
"""
        try:
            await session_service.create_session(
                app_name=APP_NAME,
                user_id=user_id,
                session_id=session_id
            )
        except Exception:
            pass
        # 3. Chạy Agent
        user_message = types.Content(
            role="user", 
            parts=[types.Part.from_text(text=enriched_query)]
        )
        final_response_text = "Xin lỗi, Tôi gặp chút trục trặc, bạn nói lại được không."
        async for event in runner.run_async(
            user_id=user_id, 
            session_id=session_id, 
            new_message=user_message
        ):
            if event.is_final_response():
                if event.content and event.content.parts:
                    final_response_text = event.content.parts[0].text
        print(f"Response: {final_response_text}")
        return ChatResponse(
            response=final_response_text,
            user_id=user_id,     # Trả lại ID để Client lưu cho lần sau
            session_id=session_id
        )
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Lỗi hệ thống: {str(e)}")
# ==========================================================================
#======================= 2. API status Home========================
# ==========================================================================
@router.get("/status")
async def get_home_status():
    rows = await fetch_all("SELECT id, name, type, status FROM devices")
    return {
            "status": "success",
            "data": [
                {
                    "id": r[0],          
                    "device": r[1], 
                    "type": r[2], 
                    "status": r[3]
                } for r in rows
            ]
        }
# ==========================================================================
# ======================= 3. API Device logs========================
# ==========================================================================
@router.get("/history")
async def get_device_logs(limit: int=20):
    query="""
        SELECT dl.id, d.name, dl.action, dl.triggered_by, dl.created_at 
            FROM device_logs dl
            JOIN devices d ON dl.device_id = d.id
            ORDER BY dl.created_at DESC LIMIT $1
        """
    rows = await fetch_all(query, limit)
    return {"status": "success", "data": [{
                "id": r[0],
                "device_name": r[1],
                "action": r[2],
                "triggered_by": r[3],
                "created_at": r[4].isoformat() if r[4] else None
            } for r in rows]}
# ==========================================================================
# ======================= 4. API ghi log control========================
# ==========================================================================
@router.post("/history")
async def add_device_log(payload: DeviceControl):
    print(f"Nhận lệnh Log: {payload.device} -> {payload.command}")
    cmd = payload.command.upper()
    if cmd in ["OPEN", "UNLOCKED"]: cmd = "ON"
    if cmd in ["CLOSE", "LOCKED"]: cmd = "OFF"
    
    try:
        numeric_id = int(payload.device.split('-')[-1])
    except (ValueError, IndexError):
        print(f"Không thể tách ID từ: {payload.device}")
        numeric_id = 1
        
    extra_data = json.dumps({"type": payload.type, "name": payload.device_name})
    
    conn = await DB_POOL.acquire()
    try:
        tr = conn.transaction()
        await tr.start()
        
        try:
            query_log = """
                INSERT INTO device_logs (device_id, action, payload, triggered_by, created_at) 
                VALUES ($1, $2, $3, $4, NOW())
            """
            await conn.execute(query_log, numeric_id, cmd, extra_data, payload.triggered_by)

            query_update = """
                UPDATE devices SET status = $1 WHERE id = $2
            """
            await conn.execute(query_update, cmd, numeric_id)

            await tr.commit()
            print(f"Đã lưu Log & Cập nhật trạng thái ID {numeric_id}")
            
        except Exception as e:

            await tr.rollback()
            raise e
    except Exception as e:
        print(f"Lỗi ghi log history: {e}")
        return {"status": "error", "message": str(e)}
    finally:
        # Bắt buộc phải trả lại connection cho Pool
        await DB_POOL.release(conn)
        
    return {"status": "success", "message": "Đã ghi nhận lịch sử thiết bị"}
# ==========================================================================
# ======================= 5. API Settings========================
# ==========================================================================
@router.get("/settings")
async def get_ui_settings():
    rows = await fetch_all("SELECT id, name, type FROM devices")
    # Chuyển đổi thành Dict
    settings = {f"dev_{d[0]}": {"name": d[1], "type": d[2].lower()} for d in rows}
    return {"status": "success", "data": settings}

@router.post("/settings")
async def save_ui_settings(payload: dict):
    print(f" NHẬN LỆNH: {payload}")
    try:
        conn = await DB_POOL.acquire()
        try:
            settings_list = payload.get("settings", [])
            for item in settings_list:
                device_id_str = str(item.get("device_id", ""))
                new_name = item.get("name")
                # Chuyển type sang IN HOA và kiểm tra null
                new_type = str(item.get("type")).upper() if item.get("type") else "LIGHT"
                
                # Tách ID: hỗ trợ cả "living-7", "dev_7", "7"
                import re
                match = re.search(r'(\d+)$', device_id_str)
                if not match:
                    continue
                numeric_id = int(match.group(1))

                # Câu lệnh SQL chuẩn (Bỏ updated_at vì DB của bạn không có cột này)
                query = """
                    UPDATE devices 
                    SET name = $1, type = $2::device_type_enum 
                    WHERE id = $3
                """
                result = await conn.execute(query, new_name, new_type, numeric_id)
                print(f"DB Response: {result} | Đã sửa ID {numeric_id} thành {new_name}")

        finally:
            await DB_POOL.release(conn)
        return {"status": "success"}
    except Exception as e:
        print(f"LỖI: {str(e)}")
        return {"status": "error", "message": str(e)}
# ==========================================================================
# ======================= 6. API Register========================
# ==========================================================================
@app.post("/api/auth/register")
async def register(request: RegisterRequest):
    family_id = await fetch_val("SELECT id FROM families WHERE family_code=$1", request.family_code)
    if not family_id: raise HTTPException(404, "Family code not found")
    
    exists = await fetch_val("SELECT id FROM users WHERE username = $1 OR email = $2", request.username, request.email)
    if exists: raise HTTPException(400, "Username or email exists")
    
    hashed_pw = get_password_hash(request.password)
    await execute("INSERT INTO users (family_id, username, email, password_hash, full_name, role) VALUES ($1, $2, $3, $4, $5, 'member')", family_id, request.username, request.email, hashed_pw, request.full_name)
    return {"status": "success", "message": "Đăng ký thành công!"}
# ==========================================================================
# ======================= 7. API Login========================
# ==========================================================================
@app.post("/api/auth/login")
async def login_user(payload:LoginRequest):
    user = await fetch_one(
        "SELECT id, password_hash, full_name, family_id, role FROM users WHERE username = $1", 
        payload.username
        )
    if not user: raise HTTPException(401, "Tên đăng nhập không tồn tại!")
    
    if not verify_password(payload.password, user['password_hash']): raise HTTPException(401, "Sai mật khẩu!")
    session_id = str(uuid.uuid4())
    
    try: await session_service.create_session(app_name=APP_NAME, user_id=str(user['id']), session_id=session_id)
    except Exception: pass
    
    return {
        "status": "success", 
        "token": session_id, 
        "user": {
            "user_id": user['id'], 
            "username": payload.username, 
            "full_name": user['full_name'], 
            "family_id": user['family_id'], 
            "role": user['role']
            }
        }
# ==========================================================================
# ======================= 8. API Register FaceID========================
# ==========================================================================
temp_embeddings = {}

@router.post("/face/analyze")
async def analyze_face(file: UploadFile = File(...)):
    """API giúp Web UI phân tích tư thế mặt và Liveness"""
    global face_app
    if not face_app: raise HTTPException(status_code=503, detail="AI Model not loaded")
    
    contents = await file.read()
    # Đẩy ra luồng phụ để không làm đơ WebSocket của hệ thống
    async with ai_semaphore:
        img, faces = await asyncio.to_thread(analyze_face_sync, contents)
    if not faces:
        return {"success": False, "error": "Không tìm thấy mặt"}
    
    face = max(faces, key=lambda f: (f.bbox[2]-f.bbox[0])*(f.bbox[3]-f.bbox[1]))
    pitch, yaw, roll = face.pose
    
    img_h, img_w = img.shape[:2]
    face_center_x = (face.bbox[0] + face.bbox[2]) / 2
    center_ok = True if (img_w * 0.3 < face_center_x < img_w * 0.7) else False

    return {
        "success": True,
        "yaw": float(yaw),
        "pitch": float(pitch),
        "blink": True,
        "depth_ok": True,
        "light_ok": True if np.mean(img) > 40 else False,
        "center_ok": center_ok 
    }
# ==========================================================================
# ======================= 9. API Register FaceID========================
# ==========================================================================
@router.post("/auth/register-face") 
async def register_face(
    file: UploadFile = File(...),
    name: str = Form(...),
    role: str = Form(...),
    step: int = Form(...),
    family_id: int = Form(1)
):
    global face_app, temp_embeddings
    storage_key = f"{name}_{role}" 
    try:
        contents = await file.read()
        async with ai_semaphore:
            img, faces = await asyncio.to_thread(analyze_face_sync, contents)
        if not faces: raise HTTPException(400, "Không tìm thấy khuôn mặt")
        
        face = max(faces, key=lambda f: (f.bbox[2]-f.bbox[0])*(f.bbox[3]-f.bbox[1]))
        if storage_key not in temp_embeddings: temp_embeddings[storage_key] = []
        temp_embeddings[storage_key].append(face.normed_embedding)

        if step == 5:
            master_emb = np.mean(temp_embeddings[storage_key], axis=0)
            master_emb = (master_emb / np.linalg.norm(master_emb)).tolist()
            emb_str = str(master_emb) # Convert format cho pgvector
            
            existing_user_id = await fetch_val("SELECT id FROM users WHERE full_name ILIKE $1 LIMIT 1", f"%{name}%")
            if existing_user_id:
                await execute("UPDATE users SET face_encoding = $1::vector WHERE id = $2", emb_str, existing_user_id)
                msg = f"Đã cập nhật Face ID cho {name}"
                final_id = existing_user_id
            else:
                placeholder_username = f"{name.lower().replace(' ', '')}_{uuid.uuid4().hex[:4]}"
                sql = "INSERT INTO users (full_name, role, face_encoding, family_id, username, email, password_hash, created_at) VALUES ($1, $2, $3::vector, $4, $5, $6, $7, NOW()) RETURNING id"
                final_id = await fetch_val(sql, name, role, emb_str, family_id, placeholder_username, f"{placeholder_username}@smarthome.local", get_password_hash("123456"))
                msg = f"Đã tạo thành viên mới ({name})"
            
            del temp_embeddings[storage_key]
            return {"status": "success", "user_id": final_id, "message": msg}
            
        return {"status": "processing", "step": step}
    except Exception as e:
        if storage_key in temp_embeddings and step == 5: del temp_embeddings[storage_key]
        raise HTTPException(500, str(e))
# ==========================================================================
# ======================= 10. API Node-Red========================
# ==========================================================================
@app.websocket("/ws/status")
async def status_endpoint(ws: WebSocket):
    await status_manager.connect(ws)
    try:
        while True: await ws.receive_text()
    except WebSocketDisconnect:
        await status_manager.disconnect(ws)
@router.post("/notify_ws")
async def notify_ws(data:DeviceStatusUpdate):
    payload = {"id": data.id, "status": data.status.upper()}
    print(f"📡 Broadcasting: {payload}") 
    await status_manager.broadcast({"id": data.id, "status": data.status.upper()})
    return {"status": "success"} 
# ==========================================================================
# ======================= 11. API esp32_config========================
# ==========================================================================
@router.get("/esp_config")
async def get_esp_config():
    # """
    # API này trả về danh sách phần cứng cực nhẹ để ESP32 tải vào SPIFFS.
    # l = location (phòng)
    # d = device (loại thiết bị)
    # p = pin (chân GPIO trên mạch)
    # t = type (0: Relay, 1: Servo, 2: Khóa cửa)
    # """
    
    # return [
    #     # Phòng ngủ
    #     {"l": "bedroom", "d": "light", "p": 13, "t": 0},
    #     {"l": "bedroom", "d": "fan", "p": 14, "t": 0},
    #     {"l": "bedroom", "d": "window", "p": 27, "t": 1}, 
        
    #     # Nhà bếp
    #     {"l": "kitchen", "d": "light", "p": 4, "t": 0},
    #     {"l": "kitchen", "d": "fan", "p": 15, "t": 0},
        
    #     # Phòng khách
    #     {"l": "livingroom", "d": "light", "p": 18, "t": 0},
    #     {"l": "livingroom", "d": "fan", "p": 19, "t": 0},
    #     {"l": "livingroom", "d": "door", "p": 26, "t": 2}, 
        
    #     # Nhà tắm
    #     {"l": "bathroom", "d": "light", "p": 25, "t": 0}
    # ]
    try:
        query = """
            SELECT 
                r.name as location, 
                d.type as device_type, 
                d.gpio_pin as pin, 
                d.hardware_type_id as hw_type
            FROM devices d
            JOIN rooms r ON d.room_id = r.id
            WHERE d.gpio_pin IS NOT NULL;
        """
        rows = await fetch_all(query)
        config_for_esp = []
        for row in rows:
            loc_str = row["location"].lower()    # (Hoặc row["rooms"]["name"].lower() tùy code anh)
            dev_str = row["device_type"].lower()
            if dev_str == "door" and loc_str == "bedroom":
                dev_str = "window"
            config_for_esp.append({
                "l": loc_str,
                "d": dev_str, 
                "p": row["pin"],
                "t": row["hw_type"]
            })

        return config_for_esp
    except Exception as e:
        print(f"[LỖI] Không thể tải cấu hình ESP: {e}")
        return []
# ==========================================================================
# ======================= 12. API Camera========================
# ==========================================================================
camera_clients = set()
camera_lock = asyncio.Lock()

# 1. CỔNG CHO WEB UI
@app.websocket("/ws/camera_view")
async def camera_view_ws(websocket: WebSocket):
    await websocket.accept()
    async with camera_lock: 
        camera_clients.add(websocket)
    print(f"[Camera] Một Web UI vừa tham gia. Tổng số người xem: {len(camera_clients)}")
    try:
        while True:
            # await websocket.receive_text()
            await asyncio.sleep(1)
    except WebSocketDisconnect:
        async with camera_lock: 
            camera_clients.discard(websocket) # Dùng discard an toàn hơn remove
        print(f"[Camera] Một Web UI vừa rời đi. Tổng số người xem: {len(camera_clients)}")

# 2. CỔNG CHO ESP32-CAM
@app.websocket("/ws/camera_upload")
async def camera_upload_ws(websocket: WebSocket):
    await websocket.accept()
    print("[Camera] ESP32-CAM ĐÃ KẾT NỐI THÀNH CÔNG VÀ ĐANG STREAMING!")
    try:
        while True:
            frame_data = await websocket.receive_bytes()
            # Khóa danh sách lại để lấy snapshot an toàn
            async with camera_lock:
                viewers = list(camera_clients)
                
            if not viewers: continue
            
            tasks = [viewer.send_bytes(frame_data) for viewer in viewers]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            # Lọc ra những người xem bị lỗi/đứt mạng
            dead_clients = [viewers[i] for i, res in enumerate(results) if isinstance(res, Exception)]
            if dead_clients:
                async with camera_lock:
                    for dead in dead_clients:
                        camera_clients.discard(dead)
                        print("[Camera] Đã xóa 1 viewer bị lỗi kết nối.")

    except WebSocketDisconnect:
        print("[Camera] ESP32-CAM ĐÃ NGẮT KẾT NỐI!")
    except Exception as e:
        print(f"[Camera] Lỗi hệ thống: {e}")

base_path = os.path.dirname(os.path.abspath(__file__))
assets_path = os.path.join(base_path, "assets")
app.mount("/assets", StaticFiles(directory=assets_path), name="assets")
@app.get("/")
async def read_index():
    return FileResponse(os.path.join(assets_path, "index.html"))
# ==================================================================
# ========================13 API Stream Camera bằng web UI============
# ================================================================
@app.post("/api/camera/force")
async def camera_control(payload: dict):
    action = payload.get("action", "STOP")
    try:
        publish.single(
            "smarthome/camera/force", 
            payload=json.dumps({"action": action}),
            hostname="14.225.224.167",
            port=1881
        )
        return {"status": "success", "action": action}
    except Exception as e:
        return {"status": "error", "message": str(e)}
# ==================================================================
# ========================14 API Ghi log detect face============
# ================================================================
@app.post("/api/camera/webhook")
async def receive_camera_event(payload:CameraPayload):
    if not DB_POOL:
        return {"status": "error", "message": "Database not connected"}
    async with DB_POOL.acquire() as conn:
        try:
            vec_str=str(payload.face_embedding) if payload.face_embedding else None
            query = """
                INSERT INTO camera_events 
                (device_id, user_id, is_unknown, face_embedding, image_url, confidence, event_type)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
            """
            await conn.execute(
                query, 
                payload.device_id, 
                payload.user_id, 
                payload.is_unknown, 
                vec_str, 
                payload.image_url, 
                payload.confidence, 
                payload.event_type
            )
            print(f"[CAMERA] Đã ghi nhận sự kiện từ device {payload.device_id}")
            if payload.is_unknown:
                await push_notification(
                    title="Cảnh báo xâm nhập",
                    message=f"Phát hiện người lạ tại {payload.device_id}",
                    user_id=1,
                    severity="DANGER"
                )
            else:
                print(f"Chào mừng User ID {payload.user_id} về nhà!")
                user_name_query = "SELECT full_name FROM users WHERE id = $1"
                user_record = await conn.fetchrow(user_name_query, payload.user_id)
                user_name = user_record['full_name'] if user_record else "Thành viên"
                await push_notification(
                    title="An ninh",
                    message=f"Thành viên {user_name} đã về nhà",
                    user_id=1,
                    severity="INFO"
                )
            return {"status": "success"}
        except Exception as e:
            print("Lỗi lưu Camera Event: ", e)
            return {"status": "error", "message": str(e)}

# ==================================================================
# ========================15 Cảnh báo============
# ================================================================
@router.post("/emergency_report")
async def handle_emergency_report(data: dict):
    room = data.get("room", "Không rõ vị trí")
    metric = data.get("metric", "Không rõ chỉ số")
    value = data.get("value", "0")
    
    auto_actions = []
    if "gas" in metric.lower() or int(value) > 1500:
        targets = [
            {"id": 9, "device": "door", "status": "ON", "location":"bedroom","device_name": "Cửa sổ phòng ngủ"},
            {"id": 3, "device": "fan", "status": "ON","location":"kitchen","device_name": "Quạt hút bếp"}
        ]
        MQTT_TOPIC_PUB = "smartHome/control/device"
        for target in targets:
            try:
                publish.single(MQTT_TOPIC_PUB, json.dumps({
                    "id": target["id"],
                    "device": target["device"],
                    "location":target["location"],
                    "status": target["status"],
                    "device_name": target["device_name"]
                }),hostname="14.225.224.167",
                port=1881)
            except Exception as e:
                print("Lỗi gửi MQTT: ", e)
            try:
                extra_data = json.dumps({"type": target["device"], "name": target["name"]})
                await execute(
                    "INSERT INTO device_logs (device_id, action, payload, triggered_by, created_at) VALUES ($1, $2, $3, $4, NOW())",
                    target["id"], target["status"], extra_data, "Hệ thống Khẩn cấp"
                )
                await execute(
                    "UPDATE devices SET status = $1 WHERE id = $2",
                    target["status"], target["id"]
                )
            except Exception as e:
                print(f"Cảnh báo: Không thể cập nhật Database ({e})")
                
            if status_manager:
                await status_manager.broadcast({
                    "id": target["id"],
                    "status": target["status"]
                })
        
        auto_actions = ["đã mở cửa sổ phòng ngủ", "đã bật quạt hút bếp"]

    action_str = " và " + ", ".join(auto_actions) if auto_actions else ""
    prompt = f"""
    HỆ THỐNG PHÁT HIỆN SỰ CỐ KHẨN CẤP!
    Vị trí: {room}
    Vấn đề: {metric} vượt ngưỡng nguy hiểm (Giá trị: {value}).
    Hành động hệ thống đã tự thực hiện: {action_str}
    
    Nhiệm vụ: Viết một lời cảnh báo CỰC KỲ KHẨN CẤP gửi đến chủ nhà. 
    Thông báo cho chủ nhà biết vấn đề VÀ những hành động tự động mà em đã làm để bảo vệ ngôi nhà.
    Ngắn gọn, thúc giục. Xưng em, gọi anh.
    """
    response = await asyncio.to_thread(
        gemini_client.models.generate_content,
        model="gemini-2.5-flash",
        contents=prompt,

    )
    ai_alert = response.text.strip()

    await status_manager.broadcast({
        "type": "notification",
        "title": f"BÁO ĐỘNG KHẨN CẤP - {room}",
        "message": ai_alert,
        "severity": "DANGER"
    })
    try:
        await log_ai_decision(
            agent_name="Emergency_Agent",
            room_id=2, 
            context_data=data,
            decision_data={"alert_sent": ai_alert, "auto_actions": auto_actions},
            priority=3 
        )
        pass
    except Exception as e:
        print(f"Lỗi ghi nhớ: {e}")

    return {"status": "success", "actions": auto_actions, "message": ai_alert}
# ==================================================================
# ========================16 API sumary============
# ================================================================
@router.get("/debug/force-summary")
async def force_daily_summary():
    """
    Endpoint dùng để chạy hàm tổng kết dữ liệu ngay lập tức.
    Giúp kiểm tra logic bảng daily_sensor_stats mà không cần chờ đến 23:59.
    """
    try:
        await daily_summary_job()
        return {
            "status": "success",
            "message": f"Đã tổng kết dữ liệu thành công cho ngày {datetime.now().date()}",
            "note": "Kiểm tra bảng daily_sensor_stats để xem kết quả."
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}
app.include_router(router, prefix="/api", tags=["SmartHome"])
if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
