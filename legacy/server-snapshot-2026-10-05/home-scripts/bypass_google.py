import os

service_file = "/etc/systemd/system/filterphone-mitm.service"
with open(service_file, "r") as f:
    content = f.read()

lines = content.split('\n')
for i, line in enumerate(lines):
    if line.startswith("ExecStart=") and "--ignore-hosts" not in line:
        # מוסיפים את רשימת העקיפה לשורת ההפעלה
        ignore_flags = " --ignore-hosts '^api\\.termius\\.com:443$' --ignore-hosts '^.*\\.googleapis\\.com:443$' --ignore-hosts '^mail\\.google\\.com:443$' --ignore-hosts '^chat\\.google\\.com:443$' --ignore-hosts '^inbox\\.google\\.com:443$'"
        lines[i] = line + ignore_flags

with open("temp.service", "w") as f:
    f.write('\n'.join(lines))

os.system("sudo mv temp.service " + service_file)
os.system("sudo systemctl daemon-reload")
os.system("sudo systemctl restart filterphone-mitm")
print("✅ רשימת העקיפה הוגדרה בהצלחה! השירותים של גוגל יעבדו עכשיו חלק.")
