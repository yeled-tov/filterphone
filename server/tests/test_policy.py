import dataclasses

from filterphone.engine.policy import Action, PaintMode, Thresholds, decide, on_failure, women_among
from filterphone.engine.segment import Person, SegStats

T = Thresholds(person=0.5, female_strict=0.6, female_check=0.2, female=0.5,
               immodest=0.5, immodest_high=0.85, nsfw_check=0.15, nsfw_block=0.95)


def s(person=0.0, female=0.0, immodest=0.0, nsfw=0.0):
    return {"person": person, "female": female, "immodest": immodest, "nsfw": nsfw}


def test_level0_passes_everything():
    assert decide(0, s(1, 1, 1, 1), T).action is Action.PASS


def test_clean_landscape_passes_all_levels():
    for level in (1, 2, 3):
        assert decide(level, s(), T).action is Action.PASS


def test_strong_nsfw_blocks_without_detector():
    for level in (1, 2, 3):
        assert decide(level, s(nsfw=0.99), T).action is Action.BLOCK


def test_nsfw_gate_requests_detector_then_blocks_on_explicit():
    assert decide(3, s(nsfw=0.3), T).action is Action.NEED_DETECTOR
    dets = [("FEMALE_BREAST_EXPOSED", 0.8, 0, 0, 10, 10)]
    assert decide(3, s(nsfw=0.3), T, dets).action is Action.BLOCK
    assert decide(3, s(nsfw=0.3), T, []).action is Action.PASS


def test_explicit_detection_blocks_even_with_low_nsfw():
    dets = [("FEMALE_GENITALIA_EXPOSED", 0.7, 0, 0, 5, 5)]
    assert decide(2, s(person=0.9, female=0.9, immodest=0.9), T, dets).action is Action.BLOCK


def test_level1_blocks_women_even_modest():
    assert decide(1, s(person=0.9, female=0.9), T).action is Action.BLOCK


def test_level1_men_pass_even_shirtless():
    assert decide(1, s(person=0.9, female=0.05, immodest=0.95), T).action is Action.PASS


def test_level1_uncertain_female_checks_detector_then_each_person():
    sc = s(person=0.9, female=0.35)
    assert decide(1, sc, T).action is Action.NEED_DETECTOR
    face = [("FACE_FEMALE", 0.7, 1, 1, 5, 5)]
    assert decide(1, sc, T, face).action is Action.BLOCK
    male = [("FACE_MALE", 0.8, 1, 1, 5, 5)]
    assert decide(1, sc, T, male).action is Action.NEED_SEGMENT        # הגלאי לא הכריע — כל אדם לבד
    woman = SegStats(0.0, 0.05, people=(Person((0, 0, 30, 60), 0.9, True),))
    assert decide(1, sc, T, male, woman).action is Action.BLOCK         # אישה קטנה בתמונה גדולה
    man = SegStats(0.0, 0.0, others=0.2, people=(Person((0, 0, 30, 60), 0.05, False),))
    assert decide(1, sc, T, male, man).action is Action.PASS
    nobody = SegStats(0.0, 0.0)
    assert decide(1, sc, T, male, nobody).action is Action.PASS         # ציון בינוני ואף אדם לא נמצא
    assert decide(1, s(person=0.9, female=0.55), T, male, nobody).action is Action.BLOCK
    assert decide(1, sc, T, male, nobody, small=True).action is Action.BLOCK   # ממוזערת — לא מסתכנים


def test_level1_does_not_need_a_high_person_score():
    # אישה קטנה / חלקית (ציון "אדם" נמוך) — עדיין נבדקת, לא עוברת אוטומטית
    assert decide(1, s(person=0.2, female=0.35), T).action is Action.NEED_DETECTOR


def test_level2_modest_woman_shows_but_her_face_is_painted():
    sc = s(person=0.9, female=0.9, immodest=0.1)
    assert decide(2, sc, T).action is Action.NEED_DETECTOR          # לבוש "צנוע" לפי הציון — עדיין נבדק
    covered = SegStats(skin=0.002, person=0.3, people=(Person((0, 0, 200, 400), 0.9, True),))
    d = decide(2, sc, T, [FACE], covered)
    assert d.action is Action.PAINT and d.mode is PaintMode.SKIN    # מסכת "עור" כוללת את הפנים
    assert d.boxes == ((0, 0, 5, 5),) and d.keep == ()              # והפנים מהגלאי משלימות אותה


def test_level2_mostly_exposed_woman_blocks_the_whole_image():
    sc = s(person=0.9, female=0.9, immodest=0.6)
    assert decide(2, sc, T, [FACE], SegStats(skin=0.7, person=0.3)).action is Action.BLOCK
    assert decide(2, sc, T, [FACE], SegStats(skin=0.95, person=0.3)).action is Action.BLOCK
    d = decide(2, sc, T, [FACE], SegStats(skin=0.69, person=0.3))
    assert d.action is Action.PAINT and d.mode is PaintMode.BODY    # מתחת ל-70% — צובעים את כל הגוף


def test_level2_men_pass_without_any_model_run():
    assert decide(2, s(person=0.9, female=0.05, immodest=0.95), T).action is Action.PASS


def test_level2_flow_detector_then_segmentation_then_paint_skin():
    sc = s(person=0.9, female=0.9, immodest=0.7)
    assert decide(2, sc, T).action is Action.NEED_DETECTOR
    dets = [("BELLY_EXPOSED", 0.6, 10, 20, 30, 40), FACE]
    assert decide(2, sc, T, dets).action is Action.NEED_SEGMENT
    d = decide(2, sc, T, dets, SegStats(skin=0.2, person=0.3))
    assert d.action is Action.PAINT and d.mode is PaintMode.SKIN
    assert d.boxes == ((10, 20, 30, 40), (0, 0, 5, 5))   # משלים את המסכה (בתוך שטח האדם) — גם הפנים
    assert d.keep == ()


FACE = ("FACE_FEMALE", 0.9, 0, 0, 5, 5)


def test_level2_even_a_small_exposed_part_is_painted():
    # רק בטן / רק מעל החזה: חלק קטן מהגוף, והציון של התמונה כולה נמוך — עדיין נצבע
    sc = s(person=0.9, female=0.9, immodest=0.05)
    d = decide(2, sc, T, [FACE], SegStats(skin=0.03, person=0.3))
    assert d.action is Action.PAINT and d.mode is PaintMode.SKIN
    belly = [("BELLY_EXPOSED", 0.5, 10, 50, 20, 10), FACE]
    d = decide(2, sc, T, belly, SegStats(skin=0.001, person=0.3))      # המסכה פספסה — הגלאי השלים
    assert d.action is Action.PAINT and d.boxes == ((10, 50, 20, 10), (0, 0, 5, 5))


def test_level2_small_women_are_painted_whole():
    sc = s(person=0.9, female=0.9, immodest=0.05)
    small = Person((10, 10, 40, 90), 0.9, True)
    big = Person((100, 0, 200, 400), 0.9, True)
    d = decide(2, sc, T, [], SegStats(skin=0.0, person=0.2, people=(small, big)))
    assert d.action is Action.PAINT and d.fill == ((10, 10, 40, 90),)   # רק הקטנה — צללית


def test_level2_low_immodest_score_still_checked_when_skin_is_visible():
    # מתחת לסף "לבוש לא צנוע" אבל מעל שער הבדיקה — הסגמנטציה מכריעה
    sc = s(person=0.9, female=0.9, immodest=0.3)
    assert decide(2, sc, T).action is Action.NEED_DETECTOR
    d = decide(2, sc, T, [], SegStats(skin=0.15, person=0.2))
    assert d.action is Action.PAINT and d.mode is PaintMode.SKIN


def test_level2_very_exposed_paints_whole_body():
    covered = [("FEMALE_BREAST_COVERED", 0.8, 5, 5, 20, 20)]
    # הרבה עור גלוי לפי הסגמנטציה → כל הגוף (גם אם הציון לא "גבוה")
    d = decide(2, s(person=0.9, female=0.9, immodest=0.6), T, covered, SegStats(skin=0.5, person=0.3))
    assert d.action is Action.PAINT and d.mode is PaintMode.BODY
    assert d.boxes == ()                            # "מכוסה" נחשב רק כשהציון גבוה
    # ציון גבוה מאוד → כל הגוף, כולל האזורים ה"מכוסים" מהגלאי
    d = decide(2, s(person=0.9, female=0.9, immodest=0.9), T, covered, SegStats(skin=0.2, person=0.3))
    assert d.action is Action.PAINT and d.mode is PaintMode.BODY and d.boxes == ((5, 5, 20, 20),)


def test_level2_swimwear_signal_paints_whole_body():
    t = dataclasses.replace(T, immodest_high=0.998)          # כמו בראשים האמיתיים
    covered = [("BUTTOCKS_COVERED", 0.7, 5, 5, 20, 20)]
    # ציון גבוה (לא "גבוה מאוד") + אזור מכוסה מהגלאי → בגד ים → כל הגוף, גם כשהעור מעט
    d = decide(2, s(person=0.9, female=0.9, immodest=0.95), t, covered, SegStats(skin=0.2, person=0.3))
    assert d.action is Action.PAINT and d.mode is PaintMode.BODY and d.boxes == ((5, 5, 20, 20),)
    # אותו ציון בלי אזור מכוסה → רק העור
    d = decide(2, s(person=0.9, female=0.9, immodest=0.95), t, [], SegStats(skin=0.2, person=0.3))
    assert d.mode is PaintMode.SKIN


def test_level2_without_person_mask_falls_back_to_detector():
    none = SegStats(0.0, 0.0)
    assert decide(2, s(person=0.9, female=0.9, immodest=0.9), T, [], none).action is Action.BLOCK
    dets = [("BELLY_EXPOSED", 0.6, 10, 20, 30, 40)]
    d = decide(2, s(person=0.9, female=0.9, immodest=0.7), T, dets, none)
    assert d.action is Action.PAINT and d.mode is PaintMode.BOXES and d.boxes == ((10, 20, 30, 40),)
    assert decide(2, s(person=0.9, female=0.9, immodest=0.7), T, [], none).action is Action.PASS
    # פנים של אישה מהגלאי — מלבן על הפנים, גם כשהסגמנטציה לא מצאה אדם
    d = decide(2, s(person=0.9, female=0.9, immodest=0.1), T, [FACE], none)
    assert d.action is Action.PAINT and d.mode is PaintMode.BOXES and d.boxes == ((0, 0, 5, 5),)
    # תמונה ממוזערת שבה הסגמנטציה לא מצאה אף אחד — לא עוברת כמו שהיא
    assert decide(2, s(person=0.9, female=0.9, immodest=0.1), T, [], none, small=True).action is Action.BLOCK
    assert decide(2, s(person=0.9, female=0.2, immodest=0.1), T, [], none, small=True).action is Action.PASS


def test_level2_explicit_blocks_before_segmentation():
    dets = [("FEMALE_GENITALIA_EXPOSED", 0.7, 0, 0, 5, 5)]
    assert decide(2, s(person=0.9, female=0.9, immodest=0.9), T, dets).action is Action.BLOCK


def test_level3_swimwear_is_verified_for_nudity():
    sc = s(person=0.9, female=0.9, immodest=0.9)
    assert decide(3, sc, T).action is Action.NEED_DETECTOR
    assert decide(3, sc, T, []).action is Action.PASS


def test_failure_policy():
    assert on_failure(1).action is Action.BLOCK
    assert on_failure(2).action is Action.BLOCK        # רמה 2: "בלי לפספס"
    assert on_failure(3).action is Action.PASS


def test_thresholds_from_dict_ignores_unknown_keys():
    t = Thresholds.from_dict({"female": 0.42, "something_else": 1})
    assert t.female == 0.42


def test_thresholds_gate_derived_for_old_heads():
    assert Thresholds.from_dict({"immodest": 0.38}).immodest_check == 0.19
    assert Thresholds.from_dict({"immodest": 0.38, "immodest_check": 0.1}).immodest_check == 0.1


# ---------------------------------------------------------------- רמה 2: צובעים רק נשים

TW = dataclasses.replace(T, person_female=0.4, headless_female=0.3, female_strict=0.8)


def test_women_among_judges_each_person_with_a_head():
    assert women_among([0.9], [True], 0.9, TW) == [True]
    assert women_among([0.05], [True], 0.5, TW) == [False]                   # גבר לבד
    assert women_among([0.9, 0.05], [True, True], 0.9, TW) == [True, False]  # אישה וגבר — רק היא


def test_women_among_parts_without_head_follow_the_people_in_the_image():
    # רגל/יד שהסגמנטציה הפרידה: הולכת לפי מי שיש בתמונה
    assert women_among([0.9, -1.0], [True, False], 0.9, TW) == [True, True]
    assert women_among([0.05, -1.0], [True, False], 0.5, TW) == [False, False]
    # אף אחד לא מראה ראש — לפי התמונה כולה
    assert women_among([-1.0], [False], 0.35, TW) == [True]
    assert women_among([-1.0], [False], 0.2, TW) == [False]
    # מעבר למכסה (לא סווג) — כמו חלק בלי ראש
    assert women_among([0.9, -1.0], [True, True], 0.9, TW) == [True, True]


def test_women_among_never_drops_a_clear_woman_image():
    # התמונה כולה בבירור של אישה, אבל אף חיתוך לא עבר את הסף — המתאים ביותר נחשב אישה
    assert women_among([0.3, 0.1], [True, True], 0.95, TW) == [True, False]
    assert women_among([0.3, 0.1], [True, True], 0.6, TW) == [False, False]


WOMAN, MAN = (0, 0, 100, 200), (200, 0, 100, 200)


def test_level2_men_only_passes_even_when_very_exposed():
    sc = s(person=0.9, female=0.5, immodest=0.99)
    men = SegStats(0.0, 0.0, 0, 0.3, (Person(MAN, 0.05, False),))
    d = decide(2, sc, T, [], men)
    assert d.action is Action.PASS and d.reason == "men only"


def test_level2_exposed_skin_of_a_man_does_not_paint_a_modest_woman():
    sc = s(person=0.9, female=0.9, immodest=0.7)
    people = (Person(WOMAN, 0.9, True), Person(MAN, 0.05, False))
    belly_on_man = [("BELLY_EXPOSED", 0.8, 230, 80, 40, 40)]
    d = decide(2, sc, T, belly_on_man, SegStats(0.0, 0.2, 0, 0.2, people))
    assert d.action is Action.PAINT and d.boxes == ()           # רק הפנים שלה; הבטן שלו לא נספרת
    belly_on_woman = [("BELLY_EXPOSED", 0.8, 30, 80, 40, 40)]
    d = decide(2, sc, T, belly_on_woman, SegStats(0.0, 0.2, 0, 0.2, people))
    assert d.action is Action.PAINT and d.mode is PaintMode.SKIN and d.boxes == ((30, 80, 40, 40),)


def test_level2_very_exposed_score_from_someone_else_does_not_paint_a_covered_woman():
    people = (Person(WOMAN, 0.9, True), Person(MAN, 0.05, False))
    d = decide(2, s(person=0.9, female=0.9, immodest=0.99), T, [], SegStats(0.0, 0.2, 0, 0.2, people))
    assert d.action is Action.PAINT and d.mode is PaintMode.SKIN     # רק הפנים, לא כל הגוף
    d = decide(2, s(person=0.9, female=0.9, immodest=0.99), T, [], SegStats(0.2, 0.2, 0, 0.2, people))
    assert d.action is Action.PAINT and d.mode is PaintMode.BODY


def test_level2_without_person_mask_needs_a_sign_of_a_woman():
    none = SegStats(0.0, 0.0)
    belly = [("BELLY_EXPOSED", 0.6, 10, 20, 30, 40)]
    assert decide(2, s(person=0.9, female=0.3, immodest=0.99), T, belly, none).action is Action.PASS
    face = [("FACE_FEMALE", 0.7, 0, 0, 5, 5)]
    d = decide(2, s(person=0.9, female=0.3, immodest=0.7), T, belly + face, none)
    assert d.action is Action.PAINT and d.mode is PaintMode.BOXES
