import os

file_path = os.path.expanduser("~/filterphone/filterphone-system/סינון-תמונות/image_filter.py")
with open(file_path, "r", encoding="utf-8") as f:
    code = f.read()

# עדכון 10% ל-13%
code = code.replace('yolo_result.get("skin_ratio", 0.0) > 0.10:', 'filter_level == 1 and yolo_result.get("skin_ratio", 0.0) > 0.13:')
code = code.replace('skin ratio > 10%', 'skin ratio > 13%')

# הזרקת הקשקוש לשלב התעבורה
old_decide = 'should_block, reason = decide_block(yolo_result, nudity_result, filter_level)'
new_decide = '''should_block, reason = decide_block(yolo_result, nudity_result, filter_level)

            # --- לוגיקת סגמנטציה חכמה ---
            skin_r = yolo_result.get("skin_ratio", 0.0)
            should_segment = False
            
            # רמה 2: רק סגמנטציה, לא חוסם תמונות שלמות
            if filter_level == 2 and skin_r > 0.02:
                should_segment = True
                should_block = False
                
            # רמה 1: סגמנטציה בין 7% ל-13%
            elif filter_level == 1 and 0.07 <= skin_r <= 0.13:
                should_segment = True
                should_block = False

            if should_segment:
                try:
                    from skin_segmenter import segment_skin
                    seg_bytes = segment_skin(content)
                    if seg_bytes:
                        flow.response.content = seg_bytes
                        content = seg_bytes
                except Exception:
                    pass
            # ----------------------------'''

if "לוגיקת סגמנטציה חכמה" not in code:
    code = code.replace(old_decide, new_decide)

with open(file_path, "w", encoding="utf-8") as f:
    f.write(code)
