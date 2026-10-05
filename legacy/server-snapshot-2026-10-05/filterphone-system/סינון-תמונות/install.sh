#!/bin/bash
set -e
echo "מתקין תלויות סינון תמונות..."
pip3 install --break-system-packages -r requirements.txt
echo "מוריד מודלים מראש..."
python3 -c "from deepface import DeepFace; DeepFace.build_model('Gender')"
python3 -c "from nudenet import NudeDetector; NudeDetector()"
echo "התקנה הושלמה"
