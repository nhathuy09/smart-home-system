import asyncio
import struct
import pyaudio
import pvporcupine
import speech_recognition as sr
import edge_tts
import uuid
import pygame
import os
import aiohttp 
from dotenv import load_dotenv
import websockets
import json
import re 
import logging
from pathlib import Path


load_dotenv()  
logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
log = logging.getLogger("VoiceClient")

# ======================== CẤU HÌNH ========================
VOICE_NAME        = "vi-VN-HoaiMyNeural"
API_URL           = os.getenv("API_URL", "http://[IP_ADDRESS]/api/chat")
WS_STATUS_URL     = os.getenv("WS_STATUS_URL", "ws://[IP_ADDRESS]/ws/status")
PICOVOICE_ACCESS_KEY     = "35hNoAn27ughk7izLWPrRVnsUiab4kmqNiakH2tIsaRQ3gSL3fXrRg=="
KEYWORD_PATH      = "./keywword/hey-home_en_windows_v3_0_0.ppn"
BEEP_PATH         = "./sounds/google_home_beep.wav"
SOUNDS_DIR        = Path("./sounds")

USER_ID           = "client_voice_master"
SESSION_ID = f"voice_session_{uuid.uuid4().hex[:8]}"

alert_queue = asyncio.Queue()
speak_lock = asyncio.Lock()
pygame.mixer.init()

# ======================== HÀM XỬ LÝ ÂM THANH ========================
def _safe_remove(path: str, retries: int = 3):
    for _ in range(retries):
        try:
            if os.path.exists(path):
                os.remove(path)
            return
        except Exception:
            import time; time.sleep(0.1)

async def speak(text: str, timeout: float = 30.0):  # Đã sửa lỗi tex -> text
    """Chuyển văn bản thành giọng nói dùng Edge-TTS và phát ngay lập tức."""
    if not text: 
        return
    clean_text = re.sub(r"[*#_`]", "", text)          # strip markdown
    clean_text = re.sub(r"\s+", " ", clean_text).strip()
    log.info(f"[SAGE AI]: {clean_text}")
    
    filename = str(SOUNDS_DIR / f"voice_{uuid.uuid4().hex}.mp3")
    async with speak_lock:
        try:
            communicate = edge_tts.Communicate(clean_text, VOICE_NAME)
            await asyncio.wait_for(communicate.save(filename), timeout=timeout)
            if not os.path.exists(filename) or os.path.getsize(filename) == 0:
                log.warning("Edge-TTS trả về file rỗng.")
                return
            pygame.mixer.music.stop() 
            pygame.mixer.music.unload()
            pygame.mixer.music.load(filename)
            pygame.mixer.music.play()
            while pygame.mixer.music.get_busy():
                await asyncio.sleep(0.05)
        except asyncio.TimeoutError:
            log.error("speak() timeout — bỏ qua câu này.")
        except Exception as e:
            log.error(f"Lỗi Edge-TTS: {e}")
        finally:
            pygame.mixer.music.stop()
            try:
                pygame.mixer.music.unload()
            except Exception:
                pass 
            _safe_remove(filename)

async def play_beep():
    try:
        if os.path.exists(BEEP_PATH):
            pygame.mixer.music.load(BEEP_PATH)
            pygame.mixer.music.play()
            while pygame.mixer.music.get_busy():
                await asyncio.sleep(0.1)
    except Exception as e:
        print(f"Lỗi phát beep: {e}")

def listen_command_sync(timeout: int = 5, phrase_limit: int = 10) -> str | None:
    """Hàm nghe lệnh đồng bộ"""
    recognizer = sr.Recognizer()
    with sr.Microphone() as source:
        log.info(">>> ĐANG NGHE... (Mời bạn nói)")
        recognizer.adjust_for_ambient_noise(source, duration=0.4)
        try:
            audio = recognizer.listen(source, timeout=timeout, phrase_time_limit=phrase_limit)
            log.info(">>> ĐANG DỊCH...")
            text = recognizer.recognize_google(audio, language="vi-VN")
            log.info(f"[BẠN]: {text}")
            return text   
        except sr.WaitTimeoutError:
            log.info("--- Hết giờ, không nghe thấy gì ---")
        except sr.UnknownValueError:
            log.info("--- Không nhận ra giọng nói ---")
        except Exception as e:
            print(f"Lỗi Mic: {e}")
    return None

# ======================== GIAO TIẾP VỚI AI ========================
async def call_agent(session: aiohttp.ClientSession, query: str) -> str:
    """Gửi query lên AI agent, trả về response text."""
    payload = {"query": query, "user_id": USER_ID, "session_id": SESSION_ID}
    try:
        async with session.post(API_URL, json=payload, timeout=aiohttp.ClientTimeout(total=20)) as resp:
            if resp.status == 200:
                data = await resp.json()
                return data.get("response", "Xin lỗi, AI không phản hồi ạ.")
            log.error(f"API lỗi HTTP {resp.status}")
            return "Lỗi kết nối đến trung tâm điều khiển."
    except asyncio.TimeoutError:
        return "Trung tâm điều khiển phản hồi chậm quá, anh thử lại nhé."
    except aiohttp.ClientError as e:
        log.error(f"Lỗi mạng: {e}")
        return "Mất kết nối mạng."

async def conversation_loop(session: aiohttp.ClientSession, context_msg: str | None = None):
    """Hội thoại liên tục đến khi người dùng im lặng."""
    while True:
        user_text = await asyncio.to_thread(listen_command_sync)
        if not user_text:
            log.info(">>> Kết thúc hội thoại do im lặng.")
            break
 
        # Nếu đang trong ngữ cảnh cảnh báo, làm giàu query
        if context_msg:
            query = (
                f"Anh vừa trả lời câu hỏi của em: '{user_text}'. "
                f"(Bối cảnh: Trước đó em đã cảnh báo: '{context_msg}')"
            )
            context_msg = None  # chỉ đính kèm context cho câu đầu tiên
        else:
            query = user_text
 
        reply = await call_agent(session, query)
        await speak(reply)

# ======================== LUỒNG CẢNH BÁO NGẦM ========================
async def listen_proactive_alerts():
    """Luồng nền: lắng nghe WebSocket từ server."""
    while True:
        try:
            async with websockets.connect(WS_STATUS_URL, ping_interval=20, ping_timeout=10) as ws:
                log.info("[WS] Đã kết nối cổng cảnh báo ngầm.")
                while True:
                    raw = await ws.recv()
                    data = json.loads(raw)
                    if data.get("type") == "notification" and data.get("message"):
                        log.info(f"[CẢNH BÁO TỪ AI]: {data['message']}")
                        await alert_queue.put(data["message"])
        except websockets.ConnectionClosed:
            log.warning("[WS] Kết nối bị đóng.")
        except Exception as e:
            log.warning(f"[WS] Lỗi: {e}")
        log.info("[WS] Thử lại sau 5 giây...")
        await asyncio.sleep(5)

# ======================== CHƯƠNG TRÌNH CHÍNH ========================
async def main():
    porcupine = None
    audio_stream = None
    pa = None
    
    # Đảm bảo thư mục âm thanh tồn tại
    SOUNDS_DIR.mkdir(parents=True, exist_ok=True)
    
    try:
        porcupine = pvporcupine.create(access_key=PICOVOICE_ACCESS_KEY, keyword_paths=[KEYWORD_PATH])
        log.info("Wake word 'Hey Home' loaded.")
    except Exception as e:
        log.error(f"Không khởi tạo được Picovoice: {e}")
        return
        
    pa = pyaudio.PyAudio()
    audio_stream = pa.open(
        rate=porcupine.sample_rate,
        channels=1,
        format=pyaudio.paInt16,
        input=True,
        frames_per_buffer=porcupine.frame_length
    )

    log.info("=" * 50)
    log.info("HỆ THỐNG SẴN SÀNG — nói 'Hey Home' để ra lệnh.")
    log.info("=" * 50)
    asyncio.create_task(listen_proactive_alerts())
    
    async with aiohttp.ClientSession() as session:
        try:
            while True:
                await asyncio.sleep(0.01)
                
                # --- Ưu tiên 1: Xử lý cảnh báo proactive ---
                if not alert_queue.empty():
                    alert_msg = await alert_queue.get()
                    audio_stream.stop_stream()
                    await play_beep()
                    await speak(alert_msg)
                    await conversation_loop(session, context_msg=alert_msg)
                    audio_stream.start_stream()
                    audio_stream.read(audio_stream.get_read_available(), exception_on_overflow=False) 
                    log.info("Quay lại chờ 'Hey Home'...")
                    continue
                    
                # --- Ưu tiên 2: Phát hiện wake word ---
                pcm = audio_stream.read(porcupine.frame_length, exception_on_overflow=False)
                pcm = struct.unpack_from("h" * porcupine.frame_length, pcm)
 
                if porcupine.process(pcm) >= 0:
                    log.info("[WAKE WORD] 'HEY HOME' detected!")
                    audio_stream.stop_stream()
                    await play_beep()
                    await conversation_loop(session)
                    audio_stream.start_stream()
                    log.info("Quay lại chờ 'Hey Home'...")

        except KeyboardInterrupt:
            log.info("Đang dừng hệ thống...")
        finally:
            if porcupine: porcupine.delete()
            if audio_stream: audio_stream.close()
            if pa: pa.terminate()
            pygame.mixer.quit()

if __name__ == "__main__":
    asyncio.run(main())