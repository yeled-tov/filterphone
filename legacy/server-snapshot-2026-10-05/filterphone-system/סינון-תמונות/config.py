"""
FilterPhone — הגדרות מרכזיות
"""
import os
from dotenv import load_dotenv
load_dotenv()

# Sightengine (ראשי — 500 בקשות/יום בחינם)
SIGHTENGINE_USER = os.getenv("SIGHTENGINE_API_USER", "REDACTED")
SIGHTENGINE_SECRET = os.getenv("SIGHTENGINE_API_SECRET", "REDACTED")

# Redis
REDIS_HOST = "127.0.0.1"
REDIS_PORT = 6379
CACHE_TTL_DAYS = 30

# סינון
MIN_IMAGE_SIZE = 1500       # לא לבדוק תמונות מתחת ל-5KB (אייקונים)
MAX_IMAGE_SIZE = 10_000_000 # לא לבדוק תמונות מעל 10MB
IMAGE_RESIZE = 320          # resize לפני ניתוח (מהירות)
REQUEST_TIMEOUT = 3         # שניות

# רמות סינון:
# רמה 1: חסימה מלאה — כל אישה נחסמת (גם לבושה)
# רמה 2: טשטוש חלקי — טשטוש עור של אישה חשופה (בגדים נשארים)
# רמה 3: צניעות בלבד — רק עירום מלא נחסם (DNS כבר חוסם פורנו)
# גבר = תמיד עובר. אפילו בלי חולצה. אפילו בבוקסר. גבר = לא מסנן.

GENDER_FEMALE_THRESHOLD_STRICT = 0.30   # רמה 1: מעל 30% סיכוי אישה = חסום
GENDER_MALE_CERTAIN = 0.75              # רק מעל 75% ודאות גבר = בטוח גבר
NSFW_THRESHOLD_LEVEL2 = 0.5             # רמה 2: חשיפה > 50%
NSFW_THRESHOLD_LEVEL3 = 0.8             # רמה 3: עירום מלא > 80%
