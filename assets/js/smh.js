// ==========================================
// 1. KẾT NỐI websocket (HIỂN THỊ DỮ LIỆU)
// ==========================================
const DOMAIN = "smarthome.vonhathuy.id.vn"; 
const WS_CONTROL_URL = `wss://${DOMAIN}/ws/ui`;
const WS_SENSOR_URL = `wss://${DOMAIN}/ws/sensor`;
const API_BASE_URL = `https://${DOMAIN}`;
const WS_STATUS_URL = `wss://${DOMAIN}/ws/status`;
const WS_CAMERA_URL = `wss://${DOMAIN}/ws/camera_view`;

let nrSocket = null;
let sensorSocket = null;
let nrReconnectTimer = null;
let sensorReconnectTimer = null;
let isUpdatingFromServer = false;
const MQTT_SENSOR = {
    // ================= PHÒNG KHÁCH =================
    "sensor/temp/living": { valId: "val-temp", format: (v) => parseFloat(v).toFixed(1) },
    "sensor/humi/living": { valId: "val-humi", format: (v) => parseInt(v) },
    "sensor/eco2/living": { valId: "val-eco2", format: (v) => parseInt(v) },
    "sensor/tvoc/living": { valId: "val-tvoc", format: (v) => parseInt(v) },
    "sensor/lux/living": { valId: "val-lux", format: (v) => parseInt(v) },

    // ================= PHÒNG NGỦ =================
    "sensor/temp/bedroom": { valId: "val-temp-room1", format: (v) => parseFloat(v).toFixed(1) },
    "sensor/humi/bedroom": { valId: "val-humi-room1", format: (v) => parseInt(v) },
    "sensor/lux/bedroom": { valId: "val-lux-room1", format: (v) => parseInt(v) },

    // ================= NHÀ BẾP =================
    "sensor/gas/kitchen": { valId: "val-gas-kitchen", format: (v) => parseInt(v) },
    "sensor/temp/kitchen": { valId: "val-temp-kitchen", format: (v) => parseFloat(v).toFixed(1) },
    "sensor/humi/kitchen": { valId: "val-humi-kitchen", format: (v) => parseInt(v) }
};
let latestEco2 = 0;
let latestTvoc = 0;
let nrPingInterval = null;
function connectWebsocket() {
    if(nrSocket && (nrSocket.readyState === nrSocket.OPEN || nrSocket.readyState === nrSocket.CONNECTING)) return;
    console.log("Websocket đang kết nối node-red.....")
    nrSocket = new WebSocket(WS_CONTROL_URL);
    nrSocket.onopen = () =>{
        console.log("Connected to Node-RED");
        nrSocket.send(JSON.stringify({action: "get_all_status"}));
        clearTimeout(nrReconnectTimer);
        nrPingInterval = setInterval(() => {
            if (nrSocket.readyState === WebSocket.OPEN) {
                nrSocket.send(JSON.stringify({ action: "ping" }));
            }
        }, 30000);
    };
    nrSocket.onclose = () => {
        console.log("Disconnected node-red, try again....")
        clearInterval(nrPingInterval);
        nrReconnectTimer = setTimeout(connectWebsocket, 5000)
    };
    nrSocket.onerror=(err)=>{
        console.error("Websocket error:",err);
        nrSocket.close();
    };
}
function connectSensorSocket(){
    if(sensorSocket && (sensorSocket.readyState === sensorSocket.OPEN || sensorSocket.readyState === sensorSocket.CONNECTING)) return;
    console.log("Websocket đang kết nối node-red sensor.....")
    sensorSocket = new WebSocket(WS_SENSOR_URL);
    sensorSocket.onopen = () =>{
        console.log("Connected to Node-RED sensor");
        clearTimeout(sensorReconnectTimer);
    };
    sensorSocket.onmessage = (event) => {
        try {
            const msg = JSON.parse(event.data);
            if (msg.topic && MQTT_SENSOR[msg.topic]) {
                const sensor = MQTT_SENSOR[msg.topic];
                const value = document.getElementById(sensor.valId);
                if (value) {
                    value.innerHTML = sensor.format(msg.payload);

                    // CẬP NHẬT LOGIC CẢNH BÁO KHÔNG KHÍ
                    if (msg.topic === "sensor/eco2/living") {
                        latestEco2 = parseInt(msg.payload);
                        updateAirAlert();
                    }
                    if (msg.topic === "sensor/tvoc/living") {
                        latestTvoc = parseInt(msg.payload);
                        updateAirAlert();
                    }
                }
            } else if (msg.topic && msg.topic.includes("motion")) {
                const roomId = msg.topic.split('/').pop();
                const val = msg.payload;
                updateMotionPill(roomId, val);
            }
        } catch (e) {
            console.error("Error parsing message:", e);
        }
    };
    sensorSocket.onclose = () => {
        console.log("❌ [Sensor] Disconnected, retrying...");
        clearTimeout(sensorReconnectTimer);
        sensorReconnectTimer = setTimeout(connectSensorSocket, 5000);
    };
    sensorSocket.onerror=(err)=>{
        console.error("Websocket error:",err);
        sensorSocket.close();
    };
}
connectWebsocket()
connectSensorSocket()

function updateMotionPill(roomId, value) {
    const isMotion = (value === "1" || value === 1 || value === "ON" || value === true || value === "true");
    const text = isMotion ? (roomId === "room2" ? "Có người" : (roomId === "living" ? "Có người" : "Có người")) : (roomId === "room2" ? "Không có" : "Không người");
    const dotId = `dot-motion-${roomId}`;
    const valId = `val-motion-${roomId}`;

    const dotEl = document.getElementById(dotId);
    const valEl = document.getElementById(valId);

    if (dotEl) {
        dotEl.classList.remove("dot-green", "dot-gray");
        dotEl.classList.add(isMotion ? "dot-green" : "dot-gray");
    }
    if (valEl) {
        valEl.innerText = text;
    }
    return text; // Return text for consistency though we update manual
}

function updateAirAlert() {
    const alertEl = document.getElementById('air-alert');
    const textEl = document.getElementById('air-alert-text');
    if (!alertEl || !textEl) return;

    let status = "good";
    let message = "Không khí trong lành, rất tốt cho sức khỏe.";
    let icon = "fa-circle-check";

    if (latestEco2 >= 1200 || latestTvoc >= 660) {
        status = "danger";
        message = "CẢNH BÁO: Ô nhiễm! Nên bật quạt hút ngay.";
        icon = "fa-triangle-exclamation";
    } else if (latestEco2 >= 800 || latestTvoc >= 220) {
        status = "warn";
        message = "Không khí hơi ngột ngạt, nên mở cửa sổ.";
        icon = "fa-circle-exclamation";
    }

    // Cập nhật UI
    alertEl.className = `air-alert-card status-${status}`;
    alertEl.innerHTML = `<i class="fa-solid ${icon}"></i> <span id="air-alert-text">${message}</span>`;
}

document.addEventListener("DOMContentLoaded", () => {
    // Simple frontend logic for login simulator
    if (localStorage.getItem('isAuthenticated') !== 'true') {
        window.location.href = 'index.html';
        return;
    }

    // Set greeting name based on login
    const savedName = localStorage.getItem('username');
    if (savedName) {
        const greetingEl = document.querySelector('.greeting');
        const avatarText = document.querySelector('.avatar-text');

        if (greetingEl) {
            greetingEl.innerHTML = `Xin chào, ${savedName}! <span>👋</span>`;
        }

        if (avatarText) {
            avatarText.innerText = savedName.substring(0, 2).toUpperCase();
        }
    }

    // Toggle Dropdown Menu
    const userAvatar = document.getElementById("user-avatar");
    const dropdown = document.getElementById("dropdown");

    userAvatar?.addEventListener("click", (e) => {
        e.stopPropagation();
        dropdown.classList.toggle("show");
    });

    document.addEventListener("click", (e) => {
        if (dropdown && !userAvatar.contains(e.target)) {
            dropdown.classList.remove("show");
        }
    });

    // ==========================================
    // 3. HIỆU ỨNG TRƯỢT PHÒNG (ROOM SLIDER)
    // ==========================================
    const roomStack = document.getElementById("room-stack");
    const btnPrev = document.getElementById("room-prev");
    const btnNext = document.getElementById("room-next");
    const dotsEl = document.getElementById("room-dots");

    // Gắn class css động cho các thẻ con trong roomStack
    const roomCards = Array.from(roomStack.children);
    roomCards.forEach(card => card.classList.add("room-slide"));

    let currentRoom = 0;

    // Khởi tạo Dots
    dotsEl.innerHTML = "";
    roomCards.forEach((_, i) => {
        const dot = document.createElement("span");
        dot.className = `w-1.5 h-1.5 rounded-full cursor-pointer transition-colors ${i === 0 ? "bg-gray-800" : "bg-gray-300"}`;
        dot.addEventListener("click", () => goToRoom(i));
        dotsEl.appendChild(dot);
    });

    function goToRoom(index) {
        roomCards.forEach((card, i) => {
            card.classList.remove("is-active", "is-prev", "is-next");
            if (i === index) card.classList.add("is-active");
            else if (i < index) card.classList.add("is-prev");
            else card.classList.add("is-next");
        });

        // Cập nhật giao diện Dots
        const allDots = Array.from(dotsEl.children);
        allDots.forEach((dot, i) => {
            dot.className = `w-1.5 h-1.5 rounded-full cursor-pointer transition-colors ${i === index ? "bg-gray-800" : "bg-gray-300"}`;
        });

        // Cập nhật Tiêu đề Phòng
        const roomTitles = [
            { text: "Phòng Ngủ", icon: "fa-bed" },
            { text: "Phòng Tắm", icon: "fa-bath" }
        ];
        const titleEl = document.querySelector('.room-card .card-title h2');
        const iconEl = document.querySelector('.room-card .title-icon i');
        if (titleEl && roomTitles[index]) titleEl.innerText = roomTitles[index].text;
        if (iconEl && roomTitles[index]) iconEl.className = `fa-solid ${roomTitles[index].icon}`;

        // Nút bấm
        if (btnPrev) btnPrev.style.opacity = index === 0 ? "0.3" : "1";
        if (btnNext) btnNext.style.opacity = index === roomCards.length - 1 ? "0.3" : "1";

        currentRoom = index;
    }

    goToRoom(0);

    btnPrev?.addEventListener("click", () => currentRoom > 0 && goToRoom(currentRoom - 1));
    btnNext?.addEventListener("click", () => currentRoom < roomCards.length - 1 && goToRoom(currentRoom + 1));

    // Vuốt cảm ứng
    let touchStartX = 0;
    roomStack.addEventListener("touchstart", (e) => touchStartX = e.touches[0].clientX, { passive: true });
    roomStack.addEventListener("touchend", (e) => {
        const diff = touchStartX - e.changedTouches[0].clientX;
        if (Math.abs(diff) < 40) return;
        if (diff > 0 && currentRoom < roomCards.length - 1) goToRoom(currentRoom + 1);
        else if (diff < 0 && currentRoom > 0) goToRoom(currentRoom - 1);
    });



    // ==========================================
    // 4. AGENT CHAT LOGIC (AI GEMINI)
    // ==========================================
    const chatContent = document.getElementById("chat-content");
    const chatInput = document.getElementById("ai_input");
    const chatSendBtn = document.getElementById("btn_send_ai");

    function addMessage(text, sender) {
        const msgDiv = document.createElement("div");
        msgDiv.className = `chat-message ${sender === "user" ? "chat-message--user" : "chat-message--bot"}`;

        if (sender === "user") {
            msgDiv.innerHTML = `<div class="message-bubble message-bubble--user">${text}</div>`;
        } else {
            msgDiv.innerHTML = `
                <div class="bot-icon"><i class="fa-solid fa-robot"></i></div>
                <div class="message-bubble message-bubble--bot ai-text"></div>
            `;
        }

        chatContent.appendChild(msgDiv);
        chatContent.scrollTop = chatContent.scrollHeight;
        return sender === "agent" ? msgDiv.querySelector('.ai-text') : msgDiv;
    }
    window.addMessage = addMessage;
    function typeEffect(element, text) {
        let i = 0;
        element.textContent = "";
        const interval = setInterval(() => {
            element.textContent += text.charAt(i);
            i++;
            if (i >= text.length) {
                clearInterval(interval);
                chatContent.scrollTop = chatContent.scrollHeight;
            }
        }, 20); // Tốc độ gõ chữ
    }
    window.typeEffect = typeEffect;
    async function sendMessageToAgent() {
        const message = chatInput.value.trim();
        if (!message) return;

        let savedUserId = localStorage.getItem("agent_user_id");
        let savedSessionId = localStorage.getItem("agent_session_id");

        // Tin nhắn User
        addMessage(message, "user");
        chatInput.value = "";

        // Reset height cho textarea
        chatInput.style.height = '46px';
        chatInput.style.overflowY = 'hidden';

        // Bong bóng chờ AI
        const aiTextContainer = addMessage("", "agent");
        aiTextContainer.textContent = "Đang xử lý...";

        try {
            const response = await fetch(API_BASE_URL + "/api/chat", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    query: message,
                    user_id: savedUserId || null,
                    session_id: savedSessionId || null,
                }),
            });

            if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
            const data = await response.json();

            localStorage.setItem("agent_user_id", data.user_id);
            localStorage.setItem("agent_session_id", data.session_id);

            typeEffect(aiTextContainer, data.response);
        } catch (error) {
            aiTextContainer.textContent = "Không thể kết nối đến AI Agent. Vui lòng kiểm tra Python Server!";
            console.error("Agent Error:", error);
        }
    }

    chatSendBtn?.addEventListener("click", sendMessageToAgent);

    // Tự động giãn chiều cao theo nội dung
    chatInput?.addEventListener("input", function () {
        this.style.height = 'auto';
        let newHeight = this.scrollHeight;
        this.style.height = newHeight + 'px';

        if (newHeight > 150) {
            this.style.overflowY = 'auto';
        } else {
            this.style.overflowY = 'hidden';
        }
    });

    chatInput?.addEventListener("keydown", (e) => {
        // Nhấn Enter để gửi (trừ khi nhấn kèm Shift)
        if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            sendMessageToAgent();
        }
    });

    // Thêm chức năng nhận diện giọng nói (Voice Chat)
    const chatVoiceBtn = document.getElementById("btn_voice_ai");
    if (chatVoiceBtn) {
        const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
        if (SpeechRecognition) {
            const recognition = new SpeechRecognition();
            recognition.lang = 'vi-VN';
            recognition.interimResults = false;
            recognition.maxAlternatives = 1;

            let isRecording = false;

            chatVoiceBtn.addEventListener('click', () => {
                if (isRecording) {
                    recognition.stop();
                    return;
                }
                recognition.start();
            });

            recognition.onstart = () => {
                isRecording = true;
                chatVoiceBtn.classList.add('recording');
                chatInput.placeholder = "Đang nghe...";
            };

            recognition.onresult = (event) => {
                const transcript = event.results[0][0].transcript;
                chatInput.value = transcript;
                sendMessageToAgent(); // Tự động gửi sau khi nói xong
            };

            recognition.onerror = (event) => {
                console.error("Lỗi nhận diện giọng nói:", event.error);
                chatInput.placeholder = "Gõ lệnh điều khiển nhà...";
                chatVoiceBtn.classList.remove('recording');
                isRecording = false;
            };

            recognition.onend = () => {
                isRecording = false;
                chatVoiceBtn.classList.remove('recording');
                chatInput.placeholder = "Gõ lệnh điều khiển nhà...";
            };
        } else {
            chatVoiceBtn.style.display = 'none';
            console.warn("Trình duyệt không hỗ trợ Web Speech API.");
        }
    }

    // ==========================================
    // 5. MOBILE CHAT TOGGLE LOGIC
    // ==========================================
    const chatBubble = document.getElementById("chat-bubble");
    const closeChatBtn = document.getElementById("close-chat");
    const agentCard = document.querySelector(".agent-card");

    chatBubble?.addEventListener("click", () => {
        agentCard?.classList.add("active");
        chatBubble.style.display = "none"; // Hide bubble when chat is open

        // Focus input after a short delay for animation
        setTimeout(() => {
            document.getElementById("ai_input")?.focus();
        }, 300);
    });

    closeChatBtn?.addEventListener("click", () => {
        agentCard?.classList.remove("active");
        // Only show bubble again if we are on mobile
        if (window.innerWidth < 1024) {
            chatBubble.style.display = "flex";
        }
    });

    // Handle window resize to reset states if needed
    window.addEventListener('resize', () => {
        if (window.innerWidth >= 1024) {
            agentCard?.classList.remove("active");
            if (chatBubble) chatBubble.style.display = "none";
        } else {
            if (!agentCard?.classList.contains("active")) {
                if (chatBubble) chatBubble.style.display = "flex";
            }
        }
    });

    // ==========================================
    // 6. ĐỒNG BỘ CẤU HÌNH THIẾT BỊ TỪ CÀI ĐẶT
    // ==========================================
    
    function syncDeviceNames() {
        const iconConfig = {
            "light": { class: "fa-regular fa-lightbulb", colorClass: "icon-yellow" },
            "fan": { class: "fa-solid fa-fan", colorClass: "icon-blue" },
            "tv": { class: "fa-solid fa-tv", colorClass: "icon-purple" },
            "ac": { class: "fa-solid fa-snowflake", colorClass: "icon-blue" },
            "plug": { class: "fa-solid fa-plug", colorClass: "icon-green" },
            "fridge": { class: "fa-solid fa-kitchen-set", colorClass: "icon-slate" },
            "microwave": { class: "fa-solid fa-fire-burner", colorClass: "icon-orange" },
            "heater": { class: "fa-solid fa-water", colorClass: "icon-orange" },
            "door": { class: "fa-solid fa-lock", colorClass: "icon-red" } // Thêm icon khóa cửa
        };

        const allToggles = document.querySelectorAll('.toggle-checkbox');
        const COLOR_CLASSES = ["icon-yellow", "icon-blue", "icon-purple", "icon-green", "icon-slate", "icon-orange", "icon-gray-fade", "icon-red", "icon-teal"];

        allToggles.forEach(toggle => {
            const key = toggle.id.replace('toggle-', '');
            const savedData = localStorage.getItem(`dev_config_${key}`);

            if (savedData) {
                try {
                    const parsed = JSON.parse(savedData);
                    const parentBlock = toggle.closest('.device-item, .qc-item, .k-device-item, .door-lock-bar');
                    if (parentBlock) {
                        const nameEl = parentBlock.querySelector('[id^="dev-name-"]');
                        const iconEl = parentBlock.querySelector('[id^="dev-icon-"]');
                        const bgEl = parentBlock.querySelector('[id^="dev-bg-"]');

                        if (nameEl && parsed.name) nameEl.innerText = parsed.name;
                        const mapping = iconConfig[parsed.type];
                        if (iconEl && mapping) {
                            iconEl.className = mapping.class;
                            if (bgEl) {
                                bgEl.classList.remove(...COLOR_CLASSES);
                                bgEl.classList.add(mapping.colorClass);
                            }
                        }
                    }
                } catch (e) { }
            }
        });
    }


    async function fetchSettingsFromServer() {
        try {
            const res = await fetch(API_BASE_URL + "/api/settings", {
                method: "GET",
                headers: {
                    "Content-Type": "application/json",
                },
            });
            if (!res.ok) {
                throw new Error(`Server trả về mã lỗi: ${res.status}`);
            }
            const json = await res.json();
            
            if (json.status === "success" && json.data && typeof json.data === "object") {
                let updated = false;
                for (const [key, val] of Object.entries(json.data)) {
                    if (val === null || val === undefined) continue;
                    
                    const dbId = key.replace('dev_', ''); 
                    const localKey = `dev_config_${dbId}`; 
                    
                    const localVal = localStorage.getItem(localKey);
                    const newVal = JSON.stringify(val);
                    if (localVal !== newVal) {
                        localStorage.setItem(localKey, newVal);
                        updated = true;
                    }
                }
                if (updated) {
                    console.log("Phát hiện thay đổi thiết bị đang đồng bộ lại giao diện");
                    if (typeof syncDeviceNames === "function") {
                        try {
                            syncDeviceNames(); 
                        } catch(e) {
                            console.error("Lỗi khi đồng bộ thiết bị:", e);
                        }
                    }
                    // Nếu ở trang chủ thì reload để ăn giao diện mới
                    if (window.location.pathname.includes("home.html") || window.location.pathname === "/") {
                        window.location.reload();
                    }
                }
            }
        } catch (e) {
            console.error("Lỗi thực tế: ", e.message);
            console.log("Dùng thẻ Cache Offline (DB Server có thể đang tắt).");
        }
    }

    window.addEventListener("storage", (e) => {
        if (e.key === "device_names_updated") syncDeviceNames();
    });

    // Khởi chạy đồng bộ ngay khi load web
    syncDeviceNames();
    fetchSettingsFromServer(); 
    // ==========================================
    // 7. CHẾ ĐỘ TỐI/SÁNG (DARK/LIGHT MODE)
    // ==========================================
    const themeToggleBtn = document.getElementById("theme-toggle");

    // Check saved theme
    if (localStorage.getItem("theme") === "dark") {
        document.documentElement.setAttribute("data-theme", "dark");
        if (themeToggleBtn) themeToggleBtn.innerText = "☀️";
    }

    themeToggleBtn?.addEventListener("click", () => {
        const currentTheme = document.documentElement.getAttribute("data-theme");
        if (currentTheme === "dark") {
            document.documentElement.removeAttribute("data-theme");
            localStorage.setItem("theme", "light");
            themeToggleBtn.innerText = "🌙";
        } else {
            document.documentElement.setAttribute("data-theme", "dark");
            localStorage.setItem("theme", "dark");
            themeToggleBtn.innerText = "☀️";
        }
    });

    // ==========================================
    // 8. GHI NHẬN LỊCH SỬ HOẠT ĐỘNG (HISTORY LOGGING)
    // ==========================================
    const historyToggles = document.querySelectorAll('.toggle-checkbox');

    historyToggles.forEach(toggle => {
        toggle.addEventListener('change', async (e) => {
            if (isUpdatingFromServer) return; // Chặn nếu đang update từ server
            
            const isChecked = e.target.checked;
            const action = isChecked ? "ON" : "OFF";
            const toggleId = e.target.id; 
            const numericId = parseInt(toggleId.replace('toggle-', ''));
            const parentBlock = e.target.closest('.device-item, .qc-item, .k-device-item, .door-lock-bar');
            let actualName = "unknown";

            if (parentBlock) {
                const nameEl = parentBlock.querySelector('[id^="dev-name-"]');
                if (nameEl && nameEl.innerText.trim() !== "") actualName = nameEl.innerText;
            }

            // --- CẬP NHẬT UI TẬP TRUNG ---
            // 1. Khóa Cửa Chính (toggle-6)
            if (toggleId === 'toggle-6') {
                updateDoorUI(isChecked, toggleId);
            }
            // 2. Cửa Sổ (toggle-9)
            if (toggleId === 'toggle-9') {
                updateDoorUI(isChecked, toggleId);
            }

            let locEn = "livingroom";
            if (e.target.closest('.quick-control-card')) {
                locEn = "livingroom";
            } else if (e.target.closest('.kitchen-card')) {
                locEn = "kitchen";
            } else if (e.target.closest('.room-slide')) {
                // Phân biệt Phòng ngủ và Phòng tắm
                const slide = e.target.closest('.room-slide');
                const index = Array.from(slide.parentNode.children).indexOf(slide);
                locEn = (index === 0) ? "bedroom" : "bathroom";
            } else if (e.target.closest('.camera-card')) {
                locEn = "livingroom";
            }

            // const isDoor = actualName.toLowerCase().includes("cửa") || actualName.toLowerCase().includes("khóa");
            if (nrSocket && nrSocket.readyState === WebSocket.OPEN) {
                nrSocket.send(JSON.stringify({
                    type: "control",
                    device: numericId,
                    device_name: actualName,
                    location: locEn,
                    command: action
                }));
            }

            try {
                await fetch(API_BASE_URL + "/api/history", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                        type: "control",
                        device: `dev-${numericId}`,
                        device_name: String(actualName),
                        command: String(action),
                        location: String(locEn),
                        triggered_by: "Web UI"
                    })
                });
                localStorage.setItem("history_updated", Date.now());
            } catch (err) {
                console.warn("Lỗi API History:", err);
            }
        });
    });

});

// ==========================================
// 9. ĐỒNG BỘ TRẠNG THÁI TỪ DATABASE (POLLING)
// ==========================================
let statusSocket;
let reconnectTimer;
function connectStatusWebSocket() {
    if(statusSocket && (statusSocket.readyState === statusSocket.OPEN || statusSocket.readyState === statusSocket.CONNECTING)) return;
    console.log("Websocket đang kết nối node-red status.....")
    statusSocket = new WebSocket(WS_STATUS_URL);
    statusSocket.onopen = function () {
        console.log("Đã kết nối WebSocket trạng thái thiết bị");
        clearTimeout(reconnectTimer);
    };

    statusSocket.onmessage = function (event) {
        console.log("DỮ LIỆU ĐỒNG BỘ TỪ SERVER:", event.data);
        try {
            const data = JSON.parse(event.data);
            if (data.type === "notification") {
                console.log("🔔 Nhận được cảnh báo AI:", data.message);
                return;
            }
            if(data.status){
                const isON = (data.status.toUpperCase() === 'ON');
                // 1. Cập nhật nút gạt (Toggle)
                const toggleBtn = document.getElementById("toggle-" + data.id);
                if (toggleBtn) {
                    toggleBtn.checked = isON;
                }
                if (data.id === 6 || data.id === 9) {
                    if (typeof updateDoorUI === "function") {
                        updateDoorUI(isON, `toggle-${data.id}`);
                        console.log(`Đã đồng bộ màu sắc cho cửa ID: ${data.id}`);
                    }
                }
            }
        } catch (e) {
            console.error("Lỗi đồng bộ WebSocket:", e);
        }
    };

    statusSocket.onclose = function (e) {
        console.log("Mất kết nối WebSocket, thử kết nối lại sau 3 giây...");
        clearTimeout(reconnectTimer);
        reconnectTimer = setTimeout(connectStatusWebSocket, 5000);
    };

    statusSocket.onerror = function (err) {
        console.error("Lỗi kết nối WebSocket:", err);
        statusSocket.close();
    };
}
connectStatusWebSocket();

async function syncInitialStatus() {
    try {
        console.log("Đang lấy trạng thái thiết bị từ Database...");
        const res = await fetch(API_BASE_URL + "/api/status");
        const json = await res.json();

        if (json.status === "success" && json.data) {
            // Bật cờ để chặn event 'change' gửi lệnh MQTT rác lúc khởi động
            isUpdatingFromServer = true;

            json.data.forEach(item => {
                const toggleBtn = document.getElementById("toggle-" + item.id);
                if (toggleBtn) {
                    const isON = (item.status.toUpperCase() === 'ON');
                    toggleBtn.checked = isON;

                    if (item.id === 6 || item.id === 9 || item.device.toLowerCase().includes("cửa")) {
                        updateDoorUI(isON, `toggle-${item.id}`); 
                    }
                }
            });

            // Tắt cờ sau khi render xong
            requestAnimationFrame(() => {
                isUpdatingFromServer = false;
            });

            console.log(" Đã đồng bộ xong trạng thái ban đầu.");
        }
    } catch (err) {
        console.warn(" Không thể lấy dữ liệu: Server chưa bật hoặc lỗi mạng.");
    }
}

// Hàm phụ để cập nhật giao diện Khóa Cửa (Dùng chung cho cả Fetch và WebSocket)
function updateDoorUI(isON, toggleId = null) {
    // A. XỬ LÝ KHÓA CỬA CHÍNH (ID 6)
    if (!toggleId || toggleId === 'toggle-6') {
        const doorText = document.getElementById('door-status-text');
        const doorIcon = document.getElementById('dev-icon-door-main');
        const doorBg = document.getElementById('dev-bg-door-main');

        if (doorText) doorText.textContent = isON ? 'Đã mở khóa' : 'Đang khóa';
        if (doorIcon) doorIcon.className = isON ? 'fa-solid fa-lock-open' : 'fa-solid fa-lock';
        if (doorBg) {
            doorBg.classList.remove('icon-red', 'icon-teal');
            doorBg.classList.add(isON ? 'icon-teal' : 'icon-red');
        }
    }

    // B. XỬ LÝ CỬA SỔ (ID 9)
    if (!toggleId || toggleId === 'toggle-9') {
        const winStatus = document.getElementById('dev-status-room1-3');
        const winIcon = document.getElementById('dev-icon-room1-3');
        const winBg = document.getElementById('dev-bg-room1-3');

        if (winStatus) winStatus.textContent = isON ? 'Đang mở' : 'Đã đóng';
        if (winIcon) winIcon.className = isON ? 'fa-solid fa-door-open' : 'fa-solid fa-door-closed';
        if (winBg) {
            winBg.classList.remove('icon-slate', 'icon-teal', 'icon-red');
            winBg.classList.add(isON ? 'icon-teal' : 'icon-red');
        }
    }
}
// ==========================================
// 10. CAMERA STREAMING & MODAL LOGIC
// ==========================================
let cameraSocket = null;
function startCameraStream() {
    const streamImg = document.getElementById('video-stream');
    const loadingView = document.getElementById('cam-loading-view');
    const realView = document.getElementById('cam-real-view');
    const camStatusBadge = document.getElementById('cam-status-badge');

    if (!streamImg) return;
    // 1. Hiển thị trạng thái đang tải
    loadingView?.classList.remove('hidden');
    realView?.classList.add('hidden');
    // 2. Khởi tạo WebSocket Camera (Sử dụng URL host hiện tại)
    console.log("[Camera] Đang kết nối tới:", WS_CAMERA_URL);

    if (cameraSocket) {
        cameraSocket.close();
    }
    cameraSocket = new WebSocket(WS_CAMERA_URL);
    cameraSocket.binaryType="blob";
    cameraSocket.onopen = () => {
        console.log("[Camera] Đã kết nối thành công!");
        if (camStatusBadge) {
            camStatusBadge.className = "badge badge-online";
            camStatusBadge.innerHTML = '<span class="dot"></span> ONLINE';
        }
    };

    cameraSocket.onmessage = (event) => {
        if (event.data instanceof Blob){
            console.log("Nhận được Frame:", event.data.size, "bytes");
            const imageBlob=new Blob([event.data],{type:"image/jpeg"});
            const objectURL=URL.createObjectURL(imageBlob);
            streamImg.src=objectURL;
            streamImg.onload=()=>{
                URL.revokeObjectURL(objectURL);
            };
            if (!loadingView?.classList.contains('hidden')) {
                loadingView?.classList.add('hidden');
                realView?.classList.remove('hidden');
            }
        }else if(typeof event.data==="string"){
            const frameData = event.data;
            streamImg.src = `data:image/jpeg;base64,${frameData}`;
            if (!loadingView?.classList.contains('hidden')) {
                loadingView?.classList.add('hidden');
                realView?.classList.remove('hidden');
            }
        }
      
    };
    cameraSocket.onclose = () => {
        console.log("[Camera] Đã ngắt kết nối.");
        if (camStatusBadge) {
            camStatusBadge.className = "badge badge-offline";
            camStatusBadge.innerHTML = '<span class="dot"></span> OFFLINE';
        }
        // Xóa ảnh khi ngắt kết nối
        streamImg.src = "";
    };

    cameraSocket.onerror = (err) => {
        console.error("[Camera] Lỗi WebSocket:", err);
    };
}

function stopCameraStream() {
    if (cameraSocket) {
        cameraSocket.close();
        cameraSocket = null;
    }
}

// Khởi tạo các sự kiện camera khi DOM sẵn sàng
document.addEventListener('DOMContentLoaded', () => {
    const btnExpandCam = document.getElementById('btn-expand-cam');
    const btnReconnectCam = document.getElementById('btn-reconnect-cam');
    const camModal = document.getElementById('cam-modal');
    const closeCamModal = document.getElementById('close-cam-modal');
    // Mở rộng Camera
    btnExpandCam?.addEventListener('click', async() => {
        camModal?.classList.add('active');
        try{
            await fetch(API_BASE_URL+"/api/camera/force",{
                method:"POST",
                headers:{
                    "Content-Type":"application/json"
                },
                body:JSON.stringify({action:"START"})
            });
        }catch(e){
            console.error("Lỗi gửi lệnh START: ",e);
        }
        startCameraStream();
    });

    // Đóng Camera
    closeCamModal?.addEventListener('click', async() => {
        camModal?.classList.remove('active');
        stopCameraStream();
        try{
            await fetch(API_BASE_URL+"/api/camera/force",{
                method:"POST",
                headers:{
                    "Content-Type":"application/json"
                },
                body:JSON.stringify({action:"STOP"})
            });
        }catch(e){
            console.error("Lỗi gửi lệnh STOP: ",e);
        }
        
    });

    // Kết nối lại từ thẻ nhỏ
    btnReconnectCam?.addEventListener('click', () => {
        const camStatusText = document.getElementById('cam-status-text');
        if (camStatusText) camStatusText.textContent = "Đang kiểm tra...";
        // Thử kết nối stream ngắn hạn hoặc chỉ cập nhật badge
        startCameraStream();
        // setTimeout(stopCameraStream, 2000); // Tắt sau 2s nếu chỉ kiểm tra từ thẻ ngoài
    });
    setInterval(() => {
        const timeEl = document.getElementById('cam-modal-time');
        if (camModal && camModal.classList.contains('active') && timeEl) {
            const now = new Date();
            timeEl.textContent = now.toLocaleTimeString('vi-VN');
        }
    }, 1000);
    syncInitialStatus();
});

