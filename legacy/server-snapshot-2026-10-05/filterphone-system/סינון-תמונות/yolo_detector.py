"""
YOLOv8-pose detector - גרסה מחמירה
זיהוי male רק אם יש ראש + ביטחון גבוה
"""
from ultralytics import YOLO
from PIL import Image
import io
import numpy as np

_model = None

def get_model():
    global _model
    if _model is None:
        _model = YOLO('yolov8n-pose.pt')
    return _model


# נקודות שלד COCO
KP_NOSE = 0
KP_L_EYE = 1
KP_R_EYE = 2
KP_L_EAR = 3
KP_R_EAR = 4
KP_L_SHOULDER = 5
KP_R_SHOULDER = 6
KP_L_HIP = 11
KP_R_HIP = 12


def analyze_body_shape(keypoints, box):
    """
    זיהוי מגדר - מחמיר מאוד עבור male
    male רק אם: ראש נראה + כתפיים רחבות בבירור + ירכיים נראות
    """
    try:
        conf_threshold = 0.3
        
        # בדיקת נקודות ראש - חייבות להיות גלויות עבור male
        head_visible = False
        head_points = [
            keypoints[KP_NOSE],
            keypoints[KP_L_EYE],
            keypoints[KP_R_EYE],
        ]
        head_conf_sum = sum(p[2] for p in head_points if p[2] > 0.2)
        if head_conf_sum > 0.7:  # לפחות שתי נקודות ראש ברורות
            head_visible = True
        
        # נקודות כתפיים וירכיים
        l_shoulder = keypoints[KP_L_SHOULDER]
        r_shoulder = keypoints[KP_R_SHOULDER]
        l_hip = keypoints[KP_L_HIP]
        r_hip = keypoints[KP_R_HIP]
        
        shoulders_visible = (l_shoulder[2] > conf_threshold and r_shoulder[2] > conf_threshold)
        hips_visible = (l_hip[2] > conf_threshold and r_hip[2] > conf_threshold)
        
        # אם אין ראש ברור - זה כמעט בוודאי לא גבר בתמונת פורטרט
        # סביר שזו תמונת גוף של אישה
        if not head_visible:
            return "female_no_head"
        
        # אם אין כתפיים או ירכיים - לא ניתן להכריע
        if not shoulders_visible or not hips_visible:
            return "uncertain"
        
        # חישוב יחסים
        shoulder_width = abs(l_shoulder[0] - r_shoulder[0])
        hip_width = abs(l_hip[0] - r_hip[0])
        
        if hip_width < 1 or shoulder_width < 1:
            return "uncertain"
        
        ratio = shoulder_width / hip_width
        
        # male רק אם יחס מובהק מאוד (כתפיים רחבות בהרבה מהירכיים)
        # ו- יש ראש ברור
        if ratio > 1.35:
            return "male"
        elif ratio < 1.0:
            return "female"
        else:
            # טווח ביניים - לא מכריע, נחשב uncertain
            return "uncertain"
    except Exception:
        return "uncertain"


def detect_persons(image_bytes):
    try:
        img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        
        if img.width < 80 or img.height < 80:
            return {"has_person": False, "num_persons": 0, "persons": [], "skipped": "too_small"}
        
        img_array = np.array(img)
        
        model = get_model()
        # imgsz 416 ו-conf 0.20 לרגישות גבוהה
        results = model(img_array, imgsz=416, verbose=False, conf=0.20)
        
        if not results or len(results) == 0:
            return {"has_person": False, "num_persons": 0, "persons": []}
        
        result = results[0]
        persons = []
        
        if result.boxes is None or len(result.boxes) == 0:
            return {"has_person": False, "num_persons": 0, "persons": []}
        
        boxes = result.boxes
        keypoints_all = result.keypoints
        
        for i in range(len(boxes)):
            cls = int(boxes.cls[i])
            if cls != 0:
                continue
            
            conf = float(boxes.conf[i])
            box = boxes.xyxy[i].tolist()
            
            body_shape = "uncertain"
            kp_list = []
            if keypoints_all is not None and i < len(keypoints_all):
                kp_data = keypoints_all.data[i].tolist()
                kp_list = kp_data
                body_shape = analyze_body_shape(kp_data, box)
            
            persons.append({
                "confidence": conf,
                "box": box,
                "body_shape": body_shape,
                "keypoints": kp_list
            })
        
        return {
            "has_person": len(persons) > 0,
            "num_persons": len(persons),
            "persons": persons
        }
    except Exception as e:
        return {"has_person": False, "num_persons": 0, "persons": [], "error": str(e)}
