from types import SimpleNamespace

from core import products as products_mod


def _sample_merged_cats():
    cats = dict(products_mod.ALL_CATEGORIES)
    cats.update({
        "adobe": ["Adobe", "❤️‍🩹"],
        "claude": ["Claude", "☀️"],
        "gmail": ["Gmail", "🥲"],
        "slide": ["Canva", "📚"],
        "veo3": ["Veo 3 + Antigravity", "😃"],
        "youtube": ["Youtube", "📱"],
        "netflix": ["Netflix", "🍿"],
    })
    return cats


def test_classify_product_matches_single_category_by_name(monkeypatch):
    fake_db = SimpleNamespace(
        get_custom_category=lambda k: None,
        get_custom_name=lambda k: None,
    )
    monkeypatch.setattr(products_mod, "db", fake_db)
    cats = _sample_merged_cats()

    assert products_mod.classify_product(
        "capcut_ca_nhan_7_day", {"name": "Capcut Cá Nhân 7 Day"}, cats
    ) == ("CapCut", "🎬", "capcut")

    assert products_mod.classify_product(
        "gpt_momo", {"name": "[KBH] GPT Trắng có TT Momo"}, cats
    ) == ("ChatGPT", "🤖", "gpt")

    assert products_mod.classify_product(
        "nordvpn_3d", {"name": "NordVPN 3 Days full warranty"}, cats
    ) == ("VPN", "🛡️", "vpn")

    assert products_mod.classify_product(
        "hv_9", {"name": "CANVA PRO SLOT 1 NĂM"}, cats
    ) == ("Canva", "📚", "slide")

    assert products_mod.classify_product(
        "hv_5", {"name": "Slot Antigravity Ultra 20x 0 Cre 1 Tháng BH28D"}, cats
    ) == ("Veo 3 + Antigravity", "😃", "veo3")


def test_classify_product_returns_khac_when_no_category_matches(monkeypatch):
    fake_db = SimpleNamespace(
        get_custom_category=lambda k: None,
        get_custom_name=lambda k: None,
    )
    monkeypatch.setattr(products_mod, "db", fake_db)
    cats = _sample_merged_cats()

    assert products_mod.classify_product(
        "grammarly_1m", {"name": "Grammarly AI Pro 1 Tháng BHF"}, cats
    ) == ("Khác", "📦", "khac")

    assert products_mod.classify_product(
        "kahoot_7d", {"name": "Kahoot Plus Gold 7 Ngày BHF"}, cats
    ) == ("Khác", "📦", "khac")


def test_classify_product_returns_khac_when_two_or_more_categories_match(monkeypatch):
    fake_db = SimpleNamespace(
        get_custom_category=lambda k: None,
        get_custom_name=lambda k: None,
    )
    monkeypatch.setattr(products_mod, "db", fake_db)
    cats = _sample_merged_cats()

    # Matches both claude and gpt
    assert products_mod.classify_product(
        "hv_api_mirai_full_10m", {"name": "API Claude + GPT | 10M Token | BH1D"}, cats
    ) == ("Khác", "📦", "khac")

    # Matches both gpt and gmail
    assert products_mod.classify_product(
        "hv_gpt_trial", {"name": "ChatGPT Gmail Trial có offer Plus 1 tháng - KBH"}, cats
    ) == ("Khác", "📦", "khac")

    # Matches both gmail and youtube
    assert products_mod.classify_product(
        "hv_gmail_yt", {"name": "Gmail Random ip 2010-20216 trial Youtube"}, cats
    ) == ("Khác", "📦", "khac")


def test_classify_product_respects_manual_custom_category_override(monkeypatch):
    custom_map = {
        "hv_api_mirai_full_50m": "gpt",
        "force_other": "khac",
    }
    fake_db = SimpleNamespace(
        get_custom_category=lambda k: custom_map.get(k),
        get_custom_name=lambda k: None,
    )
    monkeypatch.setattr(products_mod, "db", fake_db)
    cats = _sample_merged_cats()

    # Even though name matches both Claude and GPT, manual override to 'gpt' wins
    assert products_mod.classify_product(
        "hv_api_mirai_full_50m", {"name": "API Claude + GPT | 50M Token | BH2D"}, cats
    ) == ("ChatGPT", "🤖", "gpt")

    # Manual override to 'khac' keeps it in Khác even if name contains ChatGPT
    assert products_mod.classify_product(
        "force_other", {"name": "ChatGPT Special"}, cats
    ) == ("Khác", "📦", "khac")
