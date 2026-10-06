import io

import numpy as np
from PIL import Image

from filterphone.engine import segment as S


def probs_with(regions: dict[int, tuple[int, int, int, int]], size: int = 256) -> np.ndarray:
    """מפת הסתברויות מדומה: הכול רקע, חוץ ממלבנים (x0, y0, x1, y1) של סוגים אחרים."""
    p = np.zeros((size, size, S.N_CLASSES), np.float32)
    p[..., S.BG] = 1.0
    for cls, (x0, y0, x1, y1) in regions.items():
        p[y0:y1, x0:x1] = 0.0
        p[y0:y1, x0:x1, cls] = 1.0
    return p


class FakeModel:
    """מחזיר "אדם" קטן: מלבן עור במקום קבוע בכל קלט — מספיק כדי לבדוק את שני המעברים."""
    size = 64

    def __init__(self):
        self.calls = 0

    def __call__(self, x: np.ndarray) -> np.ndarray:
        assert x.shape == (64, 64, 3) and x.dtype == np.float32 and x.max() <= 1.0
        self.calls += 1
        return probs_with({S.SKIN: (28, 24, 36, 40), S.FACE: (29, 18, 35, 24)}, size=64)


def test_components_finds_separate_blobs():
    g = np.zeros((10, 10), bool)
    g[1:3, 1:3] = True
    g[6:9, 5:9] = True
    comps = sorted(S._components(g), reverse=True)
    assert [c[0] for c in comps] == [12, 4]
    assert comps[0][1:] == (6, 5, 8, 8)


def test_person_boxes_crops_only_small_people():
    small = probs_with({S.SKIN: (100, 100, 130, 160)})
    boxes = S.person_boxes(small, 1000, 800)
    assert len(boxes) == 1
    x0, y0, x1, y1 = boxes[0]
    assert 0 <= x0 < x1 <= 1000 and 0 <= y0 < y1 <= 800
    assert abs((x1 - x0) - (y1 - y0)) <= 1                  # ריבוע — בלי עיוות
    assert x0 <= 100 / 256 * 1000 and x1 >= 130 / 256 * 1000  # מכיל את האדם
    big = probs_with({S.CLOTHES: (10, 10, 240, 250)})
    assert S.person_boxes(big, 1000, 800) == []              # אדם גדול — מספיק המעבר הראשון
    assert S.person_boxes(probs_with({}), 1000, 800) == []


def test_labels_prefer_skin_when_likely():
    p = np.zeros((1, 3, S.N_CLASSES), np.float32)
    p[0, 0] = [0.6, 0, 0.4, 0, 0, 0]        # רקע מוביל, אבל עור "כמעט בטוח" → עור
    p[0, 1] = [0.7, 0, 0.3, 0, 0, 0]        # מתחת לסף העור → רקע
    p[0, 2] = [0.0, 0, 0.4, 0.6, 0, 0]      # פנים גוברות על עור
    assert S.labels_from_probs(p).tolist() == [[S.SKIN, S.BG, S.FACE]]


def test_merge_crop_replaces_coarse_pass_in_its_center():
    full = probs_with({S.SKIN: (0, 0, 256, 256)})            # מעבר גס: "הכול עור"
    crop = probs_with({S.CLOTHES: (0, 0, 256, 256)})         # חיתוך מפורט: בגד
    labels = S.merge(full, [((200, 200, 400, 400), crop)], 1000, 1000)
    assert labels.shape == (512, 512)
    c = int(300 * 512 / 1000)
    assert labels[c, c] == S.CLOTHES                         # מרכז החיתוך — לפי החיתוך
    assert labels[20, 20] == S.SKIN                          # מחוץ לחיתוך — לפי המעבר הגס


def test_analyze_runs_second_pass_for_small_person():
    m = FakeModel()
    im = Image.new("RGB", (800, 600), (120, 160, 200))
    labels, st = S.analyze(m, im)
    assert m.calls == 2 and st.crops == 1
    assert labels.shape == (384, 512)
    assert 0 < st.person < 0.2 and st.skin > 0.5


def test_stats_and_png_roundtrip():
    labels = np.zeros((40, 50), np.uint8)
    labels[10:20, 10:20] = S.SKIN
    labels[10:20, 20:30] = S.CLOTHES
    st = S.stats(labels)
    assert st.skin == 0.5 and st.person == 200 / 2000
    assert S.SegStats.load(st.dump()) == st
    png = S.encode_labels(labels)
    assert len(png) < 1000
    assert np.array_equal(S.decode_labels(png), labels)
    assert S.decode_labels(b"junk") is None


def _labels_person() -> np.ndarray:
    lab = np.zeros((100, 100), np.uint8)
    lab[10:30, 40:60] = S.FACE
    lab[30:90, 30:70] = S.CLOTHES
    lab[30:60, 15:30] = S.SKIN          # זרוע חשופה
    return lab


def test_skin_mode_paints_only_skin_and_keeps_face():
    m = np.asarray(S.region_mask(_labels_person(), "skin", (200, 200)))
    assert m[90, 45] == 255                     # הזרוע
    assert m[120, 100] == 0                     # הבגד
    assert m[40, 100] == 0                      # הפנים
    assert m[190, 190] == 0                     # הרקע


def test_body_mode_paints_clothes_too():
    m = np.asarray(S.region_mask(_labels_person(), "body", (200, 200)))
    assert m[120, 100] == 255 and m[90, 45] == 255 and m[40, 100] == 0


def test_keep_boxes_protect_faces_even_if_marked_skin():
    lab = _labels_person()
    lab[10:30, 40:60] = S.SKIN                  # המודל טעה וסימן את הפנים כעור
    m = np.asarray(S.region_mask(lab, "skin", (200, 200), keep_boxes=((80, 20, 40, 40),)))
    assert m[40, 100] == 0                      # בתוך תיבת הפנים — מוגן
    assert m[90, 45] == 255                     # הזרוע עדיין נצבעת


def test_extra_boxes_painted_only_inside_person():
    lab = _labels_person()
    box = (0, 100, 200, 30)                     # רצועה רחבה: חלקה על האדם, חלקה על הרקע
    m = np.asarray(S.region_mask(lab, "skin", (200, 200), extra_boxes=(box,)))
    assert m[115, 100] == 255                   # על האדם (בגד) — נצבע
    assert m[115, 5] == 0                       # על הרקע — לא


def test_paint_regions_outputs_jpeg_with_black_skin():
    im = Image.new("RGB", (200, 200), (230, 190, 160))
    out = Image.open(io.BytesIO(S.paint_regions(im, _labels_person(), "skin")))
    assert out.format == "JPEG" and out.size == (200, 200)
    assert max(out.getpixel((45, 90))) < 30
    assert min(out.getpixel((100, 120))) > 150


# ---------------------------------------------------------------- רק נשים נצבעות

def _two_people() -> np.ndarray:
    """שני אנשים נפרדים: משמאל — עם ראש (פנים ושיער) וזרוע חשופה; מימין — רגל בלבד (בלי ראש)."""
    lab = np.zeros((120, 240), np.uint8)
    lab[5:15, 30:50] = S.HAIR
    lab[15:35, 30:50] = S.FACE
    lab[35:110, 20:60] = S.CLOTHES
    lab[40:80, 10:20] = S.SKIN
    lab[60:110, 170:190] = S.SKIN
    return lab


def test_find_people_separates_people_and_sees_heads():
    lab = _two_people()
    ids, boxes, headed = S.find_people(lab, 480, 240)       # התמונה פי 2 ממפת התוויות
    assert ids.shape == lab.shape and set(np.unique(ids)) == {0, 1, 2}
    assert headed == [True, False]                          # הגדול (עם ראש) ראשון
    x, y, w, h = boxes[0]
    assert x <= 20 and y <= 10 and x + w >= 120 and y + h >= 220
    assert (ids[lab == S.BG] == 0).all() and (ids[50, 15] == 1) and (ids[80, 180] == 2)


def test_keep_women_marks_men_and_stats_count_only_women():
    lab = _two_people()
    ids, boxes, headed = S.find_people(lab, 240, 120)
    only_first = S.keep_women(lab, ids, {1})
    assert (only_first[60:110, 170:190] == S.MAN).all()      # השני — גבר: מסומן, לא נצבע
    assert only_first[50, 15] == S.SKIN                      # הראשון — אישה: כמו קודם
    st = S.stats(only_first)
    women = int(np.count_nonzero((lab != S.BG)[:, :100]))
    assert st.person == women / lab.size
    assert st.skin == 400 / women                            # רק העור שלה (הזרוע) — לא הרגל של הגבר
    assert st.others == 1000 / lab.size
    nobody = S.stats(S.keep_women(lab, ids, set()))
    assert nobody.person == 0 and nobody.skin == 0 and nobody.others > 0


def test_men_are_never_painted():
    lab = S.keep_women(_two_people(), S.find_people(_two_people(), 240, 120)[0], {1})
    for mode in ("skin", "body"):
        m = np.asarray(S.region_mask(lab, mode, (240, 120)))
        assert m[60, 15] == 255                              # הזרוע שלה
        assert m[80, 180] == 0                               # הרגל של הגבר
    # גם מלבן מהגלאי שנופל על הגבר לא נצבע
    m = np.asarray(S.region_mask(lab, "skin", (240, 120), extra_boxes=((160, 60, 40, 50),)))
    assert m[80, 180] == 0


def test_person_view_box_is_tight_square_of_upper_body():
    x0, y0, x1, y1 = S.person_view_box((100, 50, 40, 200), 1000, 1000)
    assert (x1 - x0) == (y1 - y0) == 140                     # 70% מהגובה, ריבוע, בלי שוליים
    assert y0 == 50 and x0 == 120 - 70                       # מתחיל בראש, ממורכז לרוחב


def test_segstats_with_people_roundtrip_and_old_format_is_recomputed():
    st = S.SegStats(0.2, 0.3, 1, 0.1, (S.Person((1, 2, 3, 4), 0.91, True), S.Person((5, 6, 7, 8), 0.04, False)))
    assert S.SegStats.load(st.dump()) == st
    assert st.women == ((1, 2, 3, 4),)
    assert S.SegStats.load({"skin": 0.2, "person": 0.3, "crops": 0}) is None   # פורמט ישן — יחושב מחדש
