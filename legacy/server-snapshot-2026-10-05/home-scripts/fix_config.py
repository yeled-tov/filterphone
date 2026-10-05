import os
file_path = os.path.expanduser("~/filterphone/filterphone-system/סינון-תמונות/config.py")
with open(file_path, "r", encoding="utf-8") as f:
    code = f.read()

old_line = "MIN_IMAGE_SIZE = 5000"
new_line = "MIN_IMAGE_SIZE = 1500"

if old_line in code:
    code = code.replace(old_line, new_line)

with open(file_path, "w", encoding="utf-8") as f:
    f.write(code)

print("✅ משקל המינימום לתמונה עודכן ל-1.5KB! הרשת הוקטנה ותופסת עכשיו הכל.")
