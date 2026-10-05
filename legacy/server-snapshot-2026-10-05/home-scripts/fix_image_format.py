import os
file_path = os.path.expanduser("~/filterphone/filterphone-system/סינון-תמונות/image_filter.py")
with open(file_path, "r", encoding="utf-8") as f:
    code = f.read()

old_line = 'return any(t in content_type.lower() for t in ["image/jpeg", "image/png", "image/webp", "image/jpg"])'
new_line = 'return "image/" in content_type.lower()'

if old_line in code:
    code = code.replace(old_line, new_line)

with open(file_path, "w", encoding="utf-8") as f:
    f.write(code)

print("✅ הגדרות פורמט תמונה עודכנו! AVIF של אליאקספרס בפנים.")
