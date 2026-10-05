"""
skin_segmenter — זיהוי עור וצביעה שחורה
ארכיטקטורה:
  1. MediaPipe Selfie Segmentation → מסכת גוף אנושי
  2. YCrCb+HSV → פיקסלי עור בתוך הגוף בלבד
  3. אם עור > 7% מהתמונה → צבע שחור → החזר

MediaPipe מבטיח שרק עור אנושי יסונן — לא מוצרים, לא ריהוט, לא רקע.
"""
import cv2
import numpy as np
import sys
import types

CENSOR_COLOR   = [0, 0, 0]
SKIN_THRESHOLD = 0.07   # 7% עור מינימום לצביעה
BODY_THRESHOLD = 0.04   # 4% גוף מינימום לאישור אדם


def _patch_tf_stub():
    """Stub out TensorFlow import that breaks MediaPipe due to protobuf conflict."""
    if "tensorflow" in sys.modules:
        return
    class _DC:
        def __getattr__(self, name):
            def deco(*a, **kw):
                if a and callable(a[0]):
                    return a[0]
                def w(f): return f
                return w
            return deco
    dc = _DC()
    tools     = types.ModuleType("tensorflow.tools")
    tools_docs = types.ModuleType("tensorflow.tools.docs")
    tools_docs.doc_controls = dc
    tools.docs = tools_docs
    tf = types.ModuleType("tensorflow")
    tf.tools   = tools
    sys.modules["tensorflow"]                    = tf
    sys.modules["tensorflow.tools"]             = tools
    sys.modules["tensorflow.tools.docs"]        = tools_docs
    sys.modules["tensorflow.tools.docs.doc_controls"] = dc


_selfie_seg = None

def _warmup():
    """טעינה מראש של המודל כדי שהבקשה הראשונה לא תאחר."""
    try:
        blank = np.zeros((100, 100, 3), np.uint8)
        _get_selfie_seg().process(blank)
    except Exception:
        pass

def _get_selfie_seg():
    global _selfie_seg
    if _selfie_seg is None:
        _patch_tf_stub()
        import mediapipe as mp
        _selfie_seg = mp.solutions.selfie_segmentation.SelfieSegmentation(
            model_selection=0  # 0=general, 1=landscape
        )
    return _selfie_seg


def detect_skin_pixels(img):
    """
    AND מדויק של YCrCb + HSV לזיהוי פיקסלי עור אנושי.

    YCrCb [Cr=133-175, Cb=77-128]: Cr≥133 מוציא שחור (Cr≈128 לשחור).
    HSV   [H=0-35, S=40-220, V=40-255]: S≥40 מוציא בז'/קרם שיין (S≈25-35),
                                          H≤35 מוציא ירוק/כחול/סגול.
    """
    ycrcb = cv2.cvtColor(img, cv2.COLOR_BGR2YCrCb)
    m_ycrcb = cv2.inRange(
        ycrcb,
        np.array([0,   133, 77 ], np.uint8),
        np.array([255, 175, 128], np.uint8)
    )
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    m_hsv = cv2.inRange(
        hsv,
        np.array([0,  40, 40 ], np.uint8),
        np.array([35, 220, 255], np.uint8)
    )
    return cv2.bitwise_and(m_ycrcb, m_hsv)


def segment_skin(image_bytes, detections=None):
    """
    מקבל bytes של תמונה, מחזיר bytes עם עור אנושי מושחר.

    אלגוריתם:
      1. MediaPipe Selfie Segmentation → מסכת גוף (מניעת false-positive על מוצרים)
      2. אם גוף < 4% → החזר None (אין אדם)
      3. זהה עור עם YCrCb+HSV בתוך מסכת הגוף בלבד
      4. ניקוי מורפולוגי
      5. אם עור < 7% → החזר None
      6. צבע שחור על פיקסלי העור → החזר
    """
    try:
        nparr = np.frombuffer(image_bytes, np.uint8)
        img   = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if img is None:
            return None

        h, w = img.shape[:2]
        if w < 60 or h < 60:
            return None

        # שלב 1: זיהוי גוף אנושי עם MediaPipe
        rgb    = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        result = _get_selfie_seg().process(rgb)
        body_float = result.segmentation_mask          # float32 [0,1]
        body_mask  = (body_float > 0.5).astype(np.uint8) * 255

        body_ratio = (body_mask > 0).sum() / (h * w)
        if body_ratio < BODY_THRESHOLD:
            print(f"[SEG] {w}x{h} no person (body={body_ratio:.1%})",
                  file=sys.stderr, flush=True)
            return None

        # שלב 2: זיהוי עור בתוך הגוף בלבד
        skin_raw  = detect_skin_pixels(img)
        skin_mask = cv2.bitwise_and(skin_raw, body_mask)

        # שלב 3: ניקוי מורפולוגי
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        skin_mask = cv2.morphologyEx(skin_mask, cv2.MORPH_CLOSE, k, iterations=2)
        skin_mask = cv2.morphologyEx(skin_mask, cv2.MORPH_OPEN,  k, iterations=1)

        skin_px    = int((skin_mask > 0).sum())
        skin_ratio = skin_px / (h * w)

        print(f"[SEG] {w}x{h} body={body_ratio:.1%} skin={skin_ratio:.1%}",
              file=sys.stderr, flush=True)

        # שלב 4: סף 7%
        if skin_ratio < SKIN_THRESHOLD:
            return None

        # שלב 5: צבע שחור
        img[skin_mask > 0] = CENSOR_COLOR

        success, encoded = cv2.imencode(
            '.jpg', img, [int(cv2.IMWRITE_JPEG_QUALITY), 85]
        )
        if success:
            print(f"[SEG] ✓ painted {skin_px}px ({skin_ratio:.0%})",
                  file=sys.stderr, flush=True)
            return encoded.tobytes()

        return None

    except Exception as e:
        print(f"[SEG] EXCEPTION: {e}", file=sys.stderr, flush=True)
        return None


# טעינת מודל מראש בזמן import
_warmup()
