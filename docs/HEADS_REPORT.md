# דוח ראשי סיווג — siglip2-base-patch32-256-20261005-1039

embedder: `siglip2-base-patch32-256`, אימון: 23998 תמונות COCO train2017, בדיקה: 5000 תמונות val2017.

| בדיקה | AUC | סף | דיוק | כיסוי | n | חיוביות |
|---|---|---|---|---|---|---|
| person (recall 95%) | 0.9922 | 0.366 | 0.9805 | 0.9501 | 4530 | 2223 |
| female_check (recall 97%) | 0.9703 | 0.1903 | 0.5208 | 0.9713 | 4086 | 696 |
| female (recall 90%) | 0.9703 | 0.3865 | 0.6518 | 0.9009 | 4086 | 696 |
| female_strict (precision 90%) | 0.9703 | 0.8634 | 0.9019 | 0.6739 | 4086 | 696 |
| immodest (recall 85%) | 0.9798 | 0.3869 | 0.06 | 0.8333 | 4530 | 18 |
| immodest_high (precision 80%) | 0.9798 | 0.9993 | 1.0 | 0.0556 | 4530 | 18 |
| nsfw gate on COCO (clean images) | — | — | — | — | {'sent_to_detector': 0.02, 'max_score': 0.9291} | |
