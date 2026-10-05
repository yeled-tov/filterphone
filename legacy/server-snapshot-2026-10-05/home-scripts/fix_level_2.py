import os
import re

file_path = os.path.expanduser("~/filterphone/filterphone-system/סינון-תמונות/image_filter.py")
with open(file_path, "r", encoding="utf-8") as f:
    code = f.read()

pattern = re.compile(r'# --- רמה 2: גמישה \(סגמנטציה\) ---.*?# --- רמה 1: קשוחה מפלדה ---', re.DOTALL)

new_logic = '''# --- רמה 2: גמישה (סגמנטציה) ---
    if filter_level == 2:
        if has_explicit:
            return True, False, "explicit nudity" # רק פורנו מובהק נחסם קומפלט
        
        # כל השאר (כולל בגדי ים ועור מעל 5%) נשלח לסיגמנטציה (ציור שחור)
        if skin_ratio > 0.05 or has_exposed_female:
            return False, True, "level 2 - segmentation"
        return False, False, "level 2 - clean"

    # --- רמה 1: קשוחה מפלדה ---'''

if pattern.search(code):
    code = pattern.sub(new_logic, code)
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(code)
    print("✅ הגדרות רמה 2 תוקנו בהצלחה!")
else:
    print("❌ שגיאה: עדיין לא מצאתי את הקוד.")
