"""
מודול זיהוי תוכן נשי — NudeNet v3 (ONNX)
מחזיר מידע מפורט על כל חלקי הגוף
"""
from nudenet import NudeDetector
import tempfile
import os

_detector = None

def get_detector():
    global _detector
    if _detector is None:
        _detector = NudeDetector()
    return _detector


# קטגוריות של NudeNet v3
EXPLICIT_EXPOSED = {
    "FEMALE_BREAST_EXPOSED",
    "FEMALE_GENITALIA_EXPOSED",
    "MALE_GENITALIA_EXPOSED",
    "BUTTOCKS_EXPOSED",
    "ANUS_EXPOSED",
}

# סימנים של תמונת אישה (גם לבושה)
FEMALE_INDICATORS = {
    "FACE_FEMALE",
    "FEMALE_BREAST_COVERED",
    "FEMALE_GENITALIA_COVERED",
    "BUTTOCKS_COVERED",
    "FEMALE_BREAST_EXPOSED",
    "FEMALE_GENITALIA_EXPOSED",
}

# אזורי עור חשוף (גם אם לא "אינטימי")
SKIN_EXPOSED = {
    "BELLY_EXPOSED",
    "ARMPITS_EXPOSED",
    "FEET_EXPOSED",
}


def analyze_nudity(image_bytes):
    """
    מחזיר ניתוח מפורט של התמונה.
    
    returns: {
        has_face_female: bool,           # זוהתה פנים של אישה
        has_exposed_female: bool,        # חשיפה נשית מפורשת
        has_covered_female_parts: bool,  # חלקי גוף נשיים גם אם מכוסים (בגד ים!)
        has_explicit_nudity: bool,       # עירום מלא כלשהו
        has_exposed_skin: bool,          # אזורי עור כללי (בטן, בתי שחי)
        female_score: float,             # ציון מסכם 0-1
        detections: list,                # זיהויים גולמיים
    }
    """
    try:
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
            f.write(image_bytes)
            tmp_path = f.name
        
        det = get_detector()
        results = det.detect(tmp_path)
        os.unlink(tmp_path)
        
        has_face_female = False
        has_exposed_female = False
        has_covered_female_parts = False
        has_explicit_nudity = False
        has_exposed_skin = False
        female_score = 0.0
        
        for item in results:
            label = item.get("class", "")
            score = item.get("score", 0)
            
            if score < 0.25:
                continue
            
            # פנים נשיות — סימן חזק
            if label == "FACE_FEMALE" and score > 0.35:
                has_face_female = True
                female_score = max(female_score, score * 0.9)
            
            # חשיפה נשית מפורשת
            if label in {"FEMALE_BREAST_EXPOSED", "FEMALE_GENITALIA_EXPOSED", "BUTTOCKS_EXPOSED"} and score > 0.4:
                has_exposed_female = True
                female_score = max(female_score, score)
            
            # חלקי גוף נשיים מכוסים — זה המפתח לבגדי ים!
            if label in {"FEMALE_BREAST_COVERED", "FEMALE_GENITALIA_COVERED", "BUTTOCKS_COVERED"} and score > 0.35:
                has_covered_female_parts = True
                female_score = max(female_score, score * 0.85)
            
            # עירום מפורש כלשהו
            if label in EXPLICIT_EXPOSED and score > 0.5:
                has_explicit_nudity = True
            
            # עור חשוף (בטן, בתי שחי)
            if label in SKIN_EXPOSED and score > 0.4:
                has_exposed_skin = True
        
        return {
            "has_face_female": has_face_female,
            "has_exposed_female": has_exposed_female,
            "has_covered_female_parts": has_covered_female_parts,
            "has_explicit_nudity": has_explicit_nudity,
            "has_exposed_skin": has_exposed_skin,
            "female_score": female_score,
            "detections": results,
        }
    except Exception as e:
        return {
            "has_face_female": False,
            "has_exposed_female": False,
            "has_covered_female_parts": False,
            "has_explicit_nudity": False,
            "has_exposed_skin": False,
            "female_score": 0.0,
            "detections": [],
            "error": str(e),
        }
