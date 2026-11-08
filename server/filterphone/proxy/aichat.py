"""סינון צ'אטים של AI בדפדפן (ChatGPT, Gemini, Claude, Copilot ועוד): בקשת הפרומפט (POST לשרת ה-AI)
נבדקת מול *אותה* רשימת מונחים של חסימת החיפוש (המונחים שהמנהל מגדיר בדשבורד). פרומפט שמכיל מונח מפורש
→ הפרוקסי מחזיר תשובת סירוב במקום לשלוח אותו. שיחה רגילה לא מושפעת כלל — נתפסים רק מונחים מהרשימה.

תמונות שה-AI יוצר עוברות ממילא את מסנן התמונות של הפרוקסי (הן נטענות כתמונות רגילות), כך שתמונה לא
ראויה נחסמת באותו מנגנון כמו כל תמונה.

כאן רק הזיהוי והחילוץ (טהור, נבדק ביחידה); החסימה עצמה ב-addon. רשימת השרתים מורחבת כאן בלי לגעת
באפליקציה שבטלפון (הכול בשרת).
"""
from __future__ import annotations

import json
import re

MAX_BODY = 200_000          # מעבר לזה לא קוראים (גוף ענק) — הפרומפט תמיד קטן בהרבה
_MAX_PARTS = 4000

# (שרת, נתיב) של בקשות שנושאות פרומפט של המשתמש. שני הביטויים חייבים להתאים.
_ENDPOINTS: tuple[tuple[re.Pattern[str], re.Pattern[str]], ...] = (
    (re.compile(r"(?:^|\.)(?:chatgpt\.com|chat\.openai\.com|openai\.com)$"),
     re.compile(r"/backend-api/.*conversation|/backend-anon/.*conversation|/v1/(?:chat/)?completions")),
    (re.compile(r"(?:^|\.)(?:gemini\.google\.com|bard\.google\.com)$"),
     re.compile(r"/_/BardChatUi/data|/BardChatUi/|StreamGenerate|GenerateContent|assistant\.lamda")),
    (re.compile(r"(?:^|\.)claude\.ai$"), re.compile(r"/api/.*/(?:completion|messages|retry)")),
    (re.compile(r"(?:^|\.)(?:perplexity\.ai|pplx\.ai)$"), re.compile(r"/rest/|/socket\.io|/api/")),
    (re.compile(r"(?:^|\.)copilot\.microsoft\.com$"), re.compile(r"/turing/|/c/api/|/sydney/")),
    (re.compile(r"(?:^|\.)(?:grok\.com|x\.ai)$"), re.compile(r"/rest/|/api/")),
    (re.compile(r"(?:^|\.)(?:deepseek\.com|chat\.deepseek\.com)$"), re.compile(r"/api/")),
    (re.compile(r"(?:^|\.)meta\.ai$"), re.compile(r"/api/|/graphql")),
    (re.compile(r"(?:^|\.)poe\.com$"), re.compile(r"/api/|gql_POST|/settings")),
    (re.compile(r"(?:^|\.)huggingface\.co$"), re.compile(r"/chat/.*/conversation")),
)


def is_prompt_request(host: str, path: str, method: str) -> bool:
    """בקשה שנושאת פרומפט של המשתמש לשרת AI (POST לשרת+נתיב מוכר). טהור."""
    if method != "POST" or not host:
        return False
    h = host.lower().rstrip(".")
    p = path.split("?", 1)[0]
    return any(hr.search(h) and pr.search(p) for hr, pr in _ENDPOINTS)


def prompt_text(body: bytes | None) -> str:
    """הטקסט לבדיקה מתוך גוף הבקשה. JSON → כל ערכי המחרוזות (שם נמצא הפרומפט); אחרת — הגוף כטקסט. חתוך."""
    if not body:
        return ""
    raw = body[:MAX_BODY]
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return raw.decode("utf-8", "ignore")
    parts: list[str] = []
    _collect(data, parts)
    return " ".join(parts)[:MAX_BODY]


def _collect(v, out: list[str], depth: int = 0) -> None:
    if depth > 10 or len(out) >= _MAX_PARTS:
        return
    if isinstance(v, str):
        out.append(v)
    elif isinstance(v, dict):
        for x in v.values():
            _collect(x, out, depth + 1)
    elif isinstance(v, (list, tuple)):
        for x in v:
            _collect(x, out, depth + 1)
