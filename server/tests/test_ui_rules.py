"""מנוע הכללים מהשרת (ui_rules): אפשרות שחוסמת את ה-Meta AI בוואטסאפ מגיעה למדיניות הטלפון ככללי ממשק,
ומדיניות בלי כללים נשארת זהה (בלי השדה)."""
from filterphone.api.catalog import UI_RULES, DeviceSettings, compile_settings


def test_meta_ai_option_compiles_to_ui_rules_for_the_phone():
    comp = compile_settings(1, DeviceSettings(options=["whatsapp_ai"]))
    dj = comp.device_json()
    rules = dj["ui_rules"]
    ids = [r["id"] for r in rules]
    assert ids == ["wa_meta_ai_button", "wa_meta_ai_screen"]
    button, screen = rules
    assert button["action"] == "cover" and "com.whatsapp" in button["packages"]
    assert "meta ai" in [d.lower() for d in button["descriptions"]]
    assert screen["action"] == "back" and "metaai" in screen["activities"]
    assert comp.needs_accessibility                              # כללים נאכפים בנגישות


def test_policy_without_rules_has_no_ui_rules_field():
    dj = compile_settings(1, DeviceSettings(options=["whatsapp_status"])).device_json()
    assert "ui_rules" not in dj                                  # בייט-תואם: מדיניות ישנה לא משתנה


def test_ui_rules_registry_is_well_formed():
    for key, rules in UI_RULES.items():
        assert rules, key
        for r in rules:
            assert r.get("id"), key
            assert r.get("action") in ("back", "home", "cover", "scroll_up"), r
            assert r.get("activities") or r.get("view_ids") or r.get("descriptions") or r.get("texts"), r
