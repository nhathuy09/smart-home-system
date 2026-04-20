import insightface
import cv2
import numpy as np 

def classify_head_pose(yaw, pitch, roll, YAW_THRESHOLD=20, PITCH_THRESHOLD=20, ROLL_THRESHOLD=20):
    if yaw < -YAW_THRESHOLD: 
        return "Looking Down" 
    elif yaw > YAW_THRESHOLD:
        return "Looking Up"
    if pitch > PITCH_THRESHOLD:
        return "Looking Left"
    elif pitch < -PITCH_THRESHOLD: 
        return "Looking Right" 
    if roll > ROLL_THRESHOLD:
        return "Tilting Left"
    elif roll < -ROLL_THRESHOLD:
        return "Tilting Right"
    return "Straight"

def test_with_camera():
    cap = cv2.VideoCapture(0)
    print("Đang nạp model...")
    detector = insightface.app.FaceAnalysis(name='buffalo_l', providers=['CPUExecutionProvider'])
    detector.prepare(ctx_id=-1, det_size=(640, 640)) # ctx_id=-1 để chạy CPU cho an toàn
    print("Model đã sẵn sàng! Bấm 'q' để thoát.")

    while True:
        ret, frame = cap.read()
        if not ret: break
        
        # Lật video như soi gương
        frame = cv2.flip(frame, 1)
        rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

        faces = detector.get(rgb_frame)
        
        if faces:
            # Lấy khuôn mặt to nhất
            face = max(faces, key=lambda f: f.bbox[2] - f.bbox[0])
            
            # SỬA Ở ĐÂY: Unpack đúng thứ tự của InsightFace [Pitch, Yaw, Roll]
            pitch, yaw, roll = face.pose
            
            # Đưa vào hàm phân loại
            predicted_class = classify_head_pose(pitch, yaw, roll)
            
            # Vẽ Box và in Text
            x1, y1, x2, y2 = map(int, face.bbox)
            cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
            
            # In ra các chỉ số thực tế để debug
            info_text = f"P(Up/Dn): {pitch:.1f} | Y(L/R): {yaw:.1f} | R: {roll:.1f}"
            cv2.putText(frame, info_text, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
            cv2.putText(frame, f"Pose: {predicted_class}", (10, 60), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 255), 2)
        
        cv2.imshow('Real-time Head Pose Classification', frame)
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break
            
    cap.release()
    cv2.destroyAllWindows()

if __name__ == '__main__':
    test_with_camera()