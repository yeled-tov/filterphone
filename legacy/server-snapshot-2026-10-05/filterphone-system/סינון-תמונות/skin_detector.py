"""
skin_detector — מחשב אחוז עור בתמונה
משתמש ב-YCrCb + HSV בשילוב AND לזיהוי מדויק
"""
import cv2
import numpy as np


def detect_skin_ratio(image_bytes):
    try:
        nparr = np.frombuffer(image_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if img is None:
            return 0.0

        img = cv2.resize(img, (200, 200))

        # YCrCb — Cr≥133 מוציא שחור (Cr≈128), Cr≤175, Cb[77,128]
        ycrcb = cv2.cvtColor(img, cv2.COLOR_BGR2YCrCb)
        m_ycrcb = cv2.inRange(
            ycrcb,
            np.array([0,   133, 77 ], np.uint8),
            np.array([255, 175, 128], np.uint8)
        )

        # HSV — AND חיובי: H[0,35] מוציא ירוק/כחול, S≥20 מוציא בז'/קרם (S≈10-18)
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        m_hsv = cv2.inRange(
            hsv,
            np.array([0,  40, 40 ], np.uint8),   # S≥40 מוציא בז'/קרם שיין (S≈25-35)
            np.array([35, 220, 255], np.uint8)
        )

        mask = cv2.bitwise_and(m_ycrcb, m_hsv)

        # ניקוי רעשים
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=2)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN,  kernel, iterations=1)

        skin_pixels = cv2.countNonZero(mask)
        total_pixels = mask.shape[0] * mask.shape[1]
        return skin_pixels / total_pixels if total_pixels > 0 else 0.0
    except Exception:
        return 0.0
