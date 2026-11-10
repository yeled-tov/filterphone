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
    assert "inapp_browser" not in dj


def test_inapp_browsers_option_sends_the_package_list_to_the_phone():
    dj = compile_settings(1, DeviceSettings(options=["inapp_browsers"])).device_json()
    assert "com.waze" in dj["inapp_browser"] and "com.tranzmate" in dj["inapp_browser"]
    assert compile_settings(1, DeviceSettings(options=["inapp_browsers"])).needs_accessibility


def test_ui_rules_registry_is_well_formed():
    for key, rules in UI_RULES.items():
        assert rules, key
        for r in rules:
            assert r.get("id"), key
            assert r.get("action") in ("back", "home", "cover", "scroll_up"), r
            assert r.get("activities") or r.get("view_ids") or r.get("descriptions") or r.get("texts"), r


def test_whatsapp_groups_option_reaches_the_phone_with_chat_signals():
    """קבוצות בוואטסאפ: "groups" ב-wa_screens + סימני זיהוי השיחה (WaChats) — נאכף בנגישות."""
    from filterphone.api.catalog import WA_CHAT_SIGNALS
    comp = compile_settings(1, DeviceSettings(options=["whatsapp_groups"]))
    dj = comp.device_json()
    assert "groups" in dj["wa_screens"] and comp.needs_accessibility
    assert dj["wa_chat"] == WA_CHAT_SIGNALS and "name_in_group" in dj["wa_chat"]["group_ids"]
    # ערוצים / טאב העדכונים — גם הם מקבלים את הסימנים (יציאה מערוץ שנפתח מקישור)
    assert "wa_chat" in compile_settings(1, DeviceSettings(options=["whatsapp_channels"])).device_json()
    assert "wa_chat" in compile_settings(1, DeviceSettings(options=["whatsapp_updates"])).device_json()
    # סטטוסים בלבד / בלי וואטסאפ — המדיניות לא משתנה
    assert "wa_chat" not in compile_settings(1, DeviceSettings(options=["whatsapp_status"])).device_json()
    assert "wa_chat" not in compile_settings(1, DeviceSettings(options=[])).device_json()
    for k, v in WA_CHAT_SIGNALS.items():
        assert v and all(isinstance(x, str) and 0 < len(x) <= 60 for x in v), k
