import dataclasses

from filterphone.engine.policy import Action, PaintMode, Thresholds, decide, on_failure
from filterphone.engine.segment import SegStats

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


def test_level1_uncertain_female_uses_detector():
    d = decide(1, s(person=0.9, female=0.35), T)
    assert d.action is Action.NEED_DETECTOR
    face = [("FACE_FEMALE", 0.7, 1, 1, 5, 5)]
    assert decide(1, s(person=0.9, female=0.35), T, face).action is Action.BLOCK
    male = [("FACE_MALE", 0.8, 1, 1, 5, 5)]
    assert decide(1, s(person=0.9, female=0.35), T, male).action is Action.PASS


def test_level2_modest_woman_passes():
    assert decide(2, s(person=0.9, female=0.9, immodest=0.1), T).action is Action.PASS


def test_level2_men_pass_without_any_model_run():
    assert decide(2, s(person=0.9, female=0.05, immodest=0.95), T).action is Action.PASS


FACE = ("FACE_FEMALE", 0.9, 0, 0, 5, 5)


def test_level2_flow_detector_then_segmentation_then_paint_skin():
    sc = s(person=0.9, female=0.9, immodest=0.7)
    assert decide(2, sc, T).action is Action.NEED_DETECTOR
    dets = [("BELLY_EXPOSED", 0.6, 10, 20, 30, 40), FACE]
    assert decide(2, sc, T, dets).action is Action.NEED_SEGMENT
    d = decide(2, sc, T, dets, SegStats(skin=0.2, person=0.3))
    assert d.action is Action.PAINT and d.mode is PaintMode.SKIN
    assert d.boxes == ((10, 20, 30, 40),)          # משלים את המסכה (בתוך שטח האדם)
    assert d.keep == ((0, 0, 5, 5),)               # הפנים לא נצבעות


def test_level2_little_exposed_skin_passes():
    sc = s(person=0.9, female=0.9, immodest=0.45)
    d = decide(2, sc, T, [FACE], SegStats(skin=0.05, person=0.3))     # ידיים וצוואר בלבד
    assert d.action is Action.PASS


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
