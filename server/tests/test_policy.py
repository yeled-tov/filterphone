from filterphone.engine.policy import Action, Thresholds, decide, on_failure

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


def test_level2_immodest_woman_is_painted():
    sc = s(person=0.9, female=0.9, immodest=0.7)
    assert decide(2, sc, T).action is Action.NEED_DETECTOR
    dets = [("BELLY_EXPOSED", 0.6, 10, 20, 30, 40), ("FACE_FEMALE", 0.9, 0, 0, 5, 5)]
    d = decide(2, sc, T, dets)
    assert d.action is Action.PAINT
    assert d.boxes == ((10, 20, 30, 40),)


def test_level2_very_immodest_without_parts_blocks():
    assert decide(2, s(person=0.9, female=0.9, immodest=0.9), T, []).action is Action.BLOCK


def test_level3_swimwear_is_verified_for_nudity():
    sc = s(person=0.9, female=0.9, immodest=0.9)
    assert decide(3, sc, T).action is Action.NEED_DETECTOR
    assert decide(3, sc, T, []).action is Action.PASS


def test_failure_policy():
    assert on_failure(1).action is Action.BLOCK
    assert on_failure(2).action is Action.PASS
    assert on_failure(3).action is Action.PASS


def test_thresholds_from_dict_ignores_unknown_keys():
    t = Thresholds.from_dict({"female": 0.42, "something_else": 1})
    assert t.female == 0.42
