
import cv2
import psycopg2
import numpy as np
import onnxruntime as ort
from insightface.app import FaceAnalysis
import faiss
import sys, os, time, json, threading, warnings
import websocket
import paho.mqtt.client as mqtt
from dotenv import load_dotenv
import asyncio
import aiohttp
import logging
from dataclasses import dataclass, field
from collections import deque


# ================= SETUP =================
load_dotenv()
warnings.filterwarnings("ignore")
logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
log = logging.getLogger("FaceSystem")

# ================= CONFIG =================
MQTT_BROKER      = os.getenv("MQTT_BROKER", "[IP_ADDRESS]")
MQTT_PORT        = int(os.getenv("MQTT_PORT", 1881))
MQTT_TOPIC       = "smarthome/door/control"
WS_CAMERA_URL    = os.getenv("WS_CAMERA_URL", "ws://[IP_ADDRESS]/ws/camera_view")
WEBHOOK_URL      = os.getenv("WEBHOOK_URL", "https://smarthome.vonhathuy.id.vn/api/camera/webhook")
SUPABASE_URL     = os.getenv("SUPABASE_URL")

SIM_THRESHOLD    = 0.60   # cosine similarity để nhận diện
LIVENESS_RATIO   = 0.12   # ngưỡng depth variance / mean
COOLDOWN         = 10.0   # giây giữa 2 lần mở cửa cùng người
VOTE_FRAMES      = 5      # số frame để vote nhận diện
TRACK_TIMEOUT    = 30.0   # giây trước khi xóa track
PROCESS_INTERVAL = 0.10   # giây giữa 2 lần xử lý frame
DEPTH_INTERVAL   = 5      # cứ N frame thì tính lại depth map
IGNORE_AFTER     = 3.0    # giây nghỉ sau khi mở cửa


# ================= MQTT =================
class MQTTService:
    def __init__(self):
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        self.client.on_disconnect = self._on_disconnect
    def _on_disconnect(self, client, userdata, rc, *args):
        if rc != 0:
            log.warning("[MQTT] Mất kết nối bất ngờ, đang thử lại...")
            self._try_connect()
    def _try_connect(self):
        for attempt in range(5):
            try:
                self.client.connect(MQTT_BROKER, MQTT_PORT, keepalive=60)
                self.client.loop_start()
                log.info("[MQTT] Kết nối thành công.")
                return
            except Exception as e:
                log.warning(f"[MQTT] Thử {attempt+1}/5 thất bại: {e}")
                time.sleep(2 ** attempt)
        log.error("[MQTT] Không thể kết nối sau 5 lần thử.")

    def connect(self):
        self._try_connect()

    def publish(self, topic: str, payload: dict):
        try:
            self.client.publish(topic, json.dumps(payload))
        except Exception as e:
            log.error(f"[MQTT] Lỗi publish: {e}")

    def open_door(self, name: str, role: str, confidence: float):
        self.publish(MQTT_TOPIC, {
            "id": 6,                                
            "device": "door",
            "location": "livingroom",
            "status": "ON",                        
            "device_name": f"Khóa cửa chính",
            "triggered_by": f"Camera ({name})",     
            "confidence": round(float(confidence), 4)
        })
        self.publish("smarthome/camera/force", {"action": "STOP"})
        log.info(f"[DOOR] Mở cửa cho {name} (sim={confidence:.2f})")
# ================= WEBSOCKET CAMERA =================
class WSFrameClient:
    """
    Nhận frame từ WebSocket server theo mô hình producer-consumer.
    Chỉ giữ frame mới nhất, tự động reconnect.
    """
    def __init__(self, url):
        self.url     = url
        self._frame  = None
        self._lock   = threading.Lock()
        self._running = True

    def on_message(self, ws, message):
        np_arr = np.frombuffer(message, np.uint8)
        frame = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
        if frame is not None:
            with self._lock:
                self._frame = frame

    def on_close(self, ws, *args):
        log.error("[WS] Mất kết nối WebSocket. Đang thử lại...")

    def on_error(self, ws, error):
        log.error(f"[WS] Lỗi: {error}")
    def start(self) -> "WSFrameClient":
        def _run():
            while self._running:
                try:
                    ws = websocket.WebSocketApp(
                        self.url,
                        on_message=self.on_message,
                        on_close=self.on_close,
                        on_error=self.on_error
                    )
                    ws.run_forever(ping_interval=20, ping_timeout=10)
                except Exception as e:
                    log.error(f"[WS] Crash: {e}")
                time.sleep(2)

        threading.Thread(target=_run, daemon=True).start()
        return self

    def read(self)-> tuple[bool, np.ndarray | None]:
        with self._lock:
            if self._frame is not None:
                frame, self._frame = self._frame, None
                return True, frame
        return False, None
    def stop(self):
        self._running = False
# ================= DEPTH / LIVENESS =================

class LivenessChecker:
    """MiDaS depth-based liveness — chạy trong thread pool."""
    def __init__(self, model_path: str = "midas_small.onnx"):
        log.info("Loading MiDaS...")
        self._sess = ort.InferenceSession(
            model_path,
            providers=["CUDAExecutionProvider", "CPUExecutionProvider"],
        )
        self._input_name = self._sess.get_inputs()[0].name
        log.info("MiDaS loaded.")
    def depth_map(self, frame: np.ndarray) -> np.ndarray:
        img = cv2.resize(frame, (256, 256))
        img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
        img = (img - [0.485, 0.456, 0.406]) / [0.229, 0.224, 0.225]
        inp = np.expand_dims(np.transpose(img, (2, 0, 1)), 0).astype(np.float32)
        return np.squeeze(self._sess.run(None, {self._input_name: inp})[0])
    def is_live(self, depth: np.ndarray, bbox: np.ndarray, frame_shape: tuple) -> bool:
        h, w    = frame_shape[:2]
        x1, y1, x2, y2 = bbox
        # Scale bbox về không gian 256x256 của depth map
        sx, sy  = 256 / w, 256 / h
        dx1 = int(max(0,   x1 * sx))
        dx2 = int(min(255, x2 * sx))
        dy1 = int(max(0,   y1 * sy))
        dy2 = int(min(255, y2 * sy))
 
        region = depth[dy1:dy2, dx1:dx2]
        if region.size == 0:
            return False
        # Std của vùng mặt phải đủ lớn so với toàn bộ depth map
        return float(np.std(region)) >= float(np.mean(depth)) * LIVENESS_RATIO
# ================= FACE ENGINE =================
@dataclass
class UserRecord:
    uid:  int
    name: str
    role: str
    emb:  np.ndarray
class FaceEngine:
    """InsightFace + FAISS — thread-safe reload."""
    def __init__(self):
        log.info("Loading InsightFace (buffalo_l)...")
        _stdout = sys.stdout
        sys.stdout = open(os.devnull, "w")
        self.app = FaceAnalysis(
            name="buffalo_l",
            providers=["CUDAExecutionProvider", "CPUExecutionProvider"],
        )
        self.app.prepare(ctx_id=0, det_size=(640, 640))
        sys.stdout = _stdout
 
        self._lock   = threading.Lock()
        self._index  = faiss.IndexFlatIP(512)
        self._users: list[UserRecord] = []
        log.info("InsightFace loaded.")
    def load_db(self):
        """Tải face encoding từ Supabase, rebuild FAISS index."""
        try:
            conn = psycopg2.connect(SUPABASE_URL)
            cur  = conn.cursor()
            cur.execute("""
                SELECT id, full_name, role, face_encoding
                FROM users
                WHERE face_encoding IS NOT NULL
            """)
            rows = cur.fetchall()
        except Exception as e:
            log.error(f"[DB] Lỗi kết nối: {e}")
            return
        finally:
            try: cur.close(); conn.close()
            except Exception: pass
 
        if not rows:
            log.warning("[DB] Không có user nào có face emcoding.")
            return
 
        records, vectors = [], []
        for uid, name, role, vec in rows:
            if isinstance(vec, str):
                vec = json.loads(vec)
            arr = np.array(vec, dtype=np.float32)
            records.append(UserRecord(uid=uid, name=name, role=role, emb=arr))
            vectors.append(arr)
 
        mat = np.array(vectors, dtype="float32")
        faiss.normalize_L2(mat)
 
        new_index = faiss.IndexFlatIP(512)
        new_index.add(mat)
        with self._lock:
            self._index = new_index
            self._users = records
 
        log.info(f"[DB] Nạp {new_index.ntotal} khuôn mặt.")
    def recognize(self, emb: np.ndarray) -> tuple[int | None, str, str, float]:
        """Trả về (uid, name, role, similarity). Thread-safe."""
        with self._lock:
            if self._index.ntotal == 0:
                return None, "Unknown", "Khach", 0.0
 
            q = emb.astype("float32").reshape(1, -1).copy()
            faiss.normalize_L2(q)
            D, I = self._index.search(q, 1)
 
        sim, idx = float(D[0][0]), int(I[0][0])
        if sim >= SIM_THRESHOLD:
            u = self._users[idx]
            return u.uid, u.name, u.role, sim
 
        return None, "Unknown", "Khach", sim
# ================= TRACKER =================
@dataclass
class Track:
    votes:     deque = field(default_factory=lambda: deque(maxlen=VOTE_FRAMES * 2))
    last_seen: float = field(default_factory=time.time)
    last_sent: float = 0.0
 
    def add(self, sim: float):
        self.last_seen = time.time()
        self.votes.append(sim)
 
    @property
    def ready(self) -> bool:
        return len(self.votes) >= VOTE_FRAMES
 
    @property
    def avg(self) -> float:
        return float(np.mean(self.votes)) if self.votes else 0.0
 
    def can_send(self) -> bool:
        return time.time() - self.last_sent >= COOLDOWN
 
    def reset(self):
        self.votes.clear()
        self.last_sent = time.time()

class TrackManager:
    def __init__(self):
        self._tracks: dict[str, Track] = {}
 
    def get(self, key: str) -> Track:
        if key not in self._tracks:
            self._tracks[key] = Track()
        return self._tracks[key]
 
    def cleanup(self):
        now  = time.time()
        dead = [k for k, t in self._tracks.items() if now - t.last_seen > TRACK_TIMEOUT]
        for k in dead:
            del self._tracks[k]
            log.debug(f"[TRACK] Xóa track '{k}' do timeout.")
# ================= WEBHOOK =================
async def send_webhook(session: aiohttp.ClientSession, payload: dict):
    try:
        async with session.post(
            WEBHOOK_URL,
            json=payload,
            timeout=aiohttp.ClientTimeout(total=5),
        ) as resp:
            log.info(f"[WEBHOOK] HTTP {resp.status}")
    except asyncio.TimeoutError:
        log.warning("[WEBHOOK] Timeout — bỏ qua.")
    except Exception as e:
        log.error(f"[WEBHOOK] Lỗi: {e}")

# ================= MAIN APP =================
async def main():
    mqtt_service = MQTTService()
    mqtt_service.connect()
    face_engine = FaceEngine()
    face_engine.load_db()
    liveness = LivenessChecker()
    tracker= TrackManager()
    cam = WSFrameClient(WS_CAMERA_URL).start()
    frame_count=0
    depth_map = None
    ignore_until    = 0.0
    last_process    = 0.0
    last_cleanup    = time.time()
    waiting_logged  = False
    log.info("=" * 50)
    log.info("HỆ THỐNG THỊ GIÁC SẴN SÀNG")
    log.info("=" * 50)
    async with aiohttp.ClientSession() as session:
        try:
            while True:
                await asyncio.sleep(0.001)
                if time.time() < ignore_until:
                    continue    
                ret, frame = cam.read()
                if not ret:
                    if not waiting_logged:
                        log.info("Đang chờ luồng ảnh...")
                        waiting_logged = True
                    await asyncio.sleep(0.05)
                    continue
                if waiting_logged:
                    log.info("Đã có luồng ảnh.")
                    waiting_logged = False
                # --- Rate limiting ---
                now = time.time()
                if now - last_process < PROCESS_INTERVAL:
                    continue
                last_process = now
                frame_count += 1
                frame = cv2.flip(frame, 1)
                faces = await asyncio.to_thread(face_engine.app.get, frame)
                if not faces:
                    continue
                if frame_count % DEPTH_INTERVAL == 0 or depth_map is None:
                    depth_map = await asyncio.to_thread(liveness.depth_map, frame)
                if now - last_cleanup > 30:
                    tracker.cleanup()
                    last_cleanup = now
                for face in faces:
                    bbox = face.bbox.astype(int)
                    if depth_map is not None and not liveness.is_live(depth_map, bbox, frame.shape):
                        log.debug("[LIVENESS] Từ chối — ảnh phẳng.")
                        continue
                    emb = face.normed_embedding.astype("float32").reshape(1, -1)
                    uid, name, role, sim = face_engine.recognize(emb)
                    track_key = f"user_{uid}" if uid else "unknown"
                    track  = tracker.get(track_key)
                    track.add(sim)
                    if not track.ready or not track.can_send():
                        continue
                    avg = track.avg
                    is_known = (name != "Unknown" and avg >= SIM_THRESHOLD)
                    asyncio.create_task(send_webhook(session, {
                        "device_id":      6,
                        "user_id":        uid,
                        "is_unknown":     not is_known,
                        "face_encoding": emb.flatten().tolist(),
                        "confidence":     round(avg, 4) if is_known else 0.0,
                        "event_type":     "FACE_DETECTED",
                    }))
                    if is_known:
                        mqtt_service.open_door(name, role, avg)
                        ignore_until = time.time() + IGNORE_AFTER
                        log.info(f"Tạm nghỉ {IGNORE_AFTER}s sau khi mở cửa...")
                    else:
                        log.warning(f"[SECURITY] Phát hiện người lạ! sim={sim:.2f}")
 
                    track.reset()
 
        except KeyboardInterrupt:
            log.info("Đang tắt hệ thống...")
        finally:
            cam.stop()
if __name__ == "__main__":
    asyncio.run(main())