"""
מודול זיהוי מגדר — DeepFace עם yunet (גרסה ממוטבת)
"""
from deepface import DeepFace
from PIL import Image
import io
import numpy as np

_model_loaded = False

def _ensure_model_loaded():
    global _model_loaded
    if not _model_loaded:
        try:
            dummy = np.zeros((224, 224, 3), dtype=np.uint8)
            DeepFace.analyze(dummy, actions=["gender"], detector_backend="yunet", enforce_detection=False, silent=True)
            _model_loaded = True
        except Exception:
            pass


def detect_gender(image_bytes):
    try:
        img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        
        if img.width < 100 or img.height < 100:
            return {"has_face": False, "faces": [], "skipped": "too_small"}
        
        max_dim = 224
        if max(img.width, img.height) > max_dim:
            img.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)
        
        img_array = np.array(img)
        
        results = DeepFace.analyze(
            img_array,
            actions=["gender"],
            detector_backend="yunet",
            enforce_detection=False,
            silent=True
        )
        
        if not isinstance(results, list):
            results = [results]
        
        faces = []
        for r in results:
            gender = r.get("dominant_gender", "Unknown")
            woman_score = r.get("gender", {}).get("Woman", 0)
            man_score = r.get("gender", {}).get("Man", 0)
            region = r.get("region", {})
            face_confidence = r.get("face_confidence", 0)
            
            if face_confidence == 0:
                continue
            
            faces.append({
                "gender": gender,
                "gender_confidence": max(woman_score, man_score),
                "woman_score": woman_score,
                "man_score": man_score,
                "is_female": woman_score > 30,
                "is_certain_male": man_score > 75,
                "region": region
            })
        
        return {"has_face": len(faces) > 0, "faces": faces}
    except Exception as e:
        return {"has_face": False, "faces": [], "error": str(e)}
