import os

service_file = "/etc/systemd/system/filterphone-mitm.service"
with open(service_file, "r") as f:
    content = f.read()

lines = content.split('\n')
for i, line in enumerate(lines):
    if line.startswith("ExecStart=") and "googleusercontent" not in line:
        lines[i] = line + " --ignore-hosts '^.*\\.googleusercontent\\.com:443$'"

with open("temp.service", "w") as f:
    f.write('\n'.join(lines))

os.system("sudo mv temp.service " + service_file)
os.system("sudo systemctl daemon-reload")
os.system("sudo systemctl restart filterphone-mitm")
print("✅ שרתי התוכן של גוגל (googleusercontent) נוספו לרשימת העקיפה!")
