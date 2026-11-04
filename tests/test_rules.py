from podagent import rules


def test_title_within_limits_passes():
    errors, warnings = rules.check_title("Sunset Print | Minimal Wall Art | Boho Poster", min_len=10)
    assert errors == [] and warnings == []


def test_title_over_140_characters_fails():
    errors, _ = rules.check_title("a" * 141)
    assert any("141 characters" in e for e in errors)


def test_title_special_characters_only_once():
    errors, _ = rules.check_title("Cats & Dogs & Birds: Print: Art")
    assert any("'&'" in e for e in errors) and any("':'" in e for e in errors)


def test_title_rejects_emoji():
    errors, _ = rules.check_title("Sunset Print 🌅")
    assert any("characters Etsy rejects" in e for e in errors)


def test_title_short_is_only_a_warning():
    errors, warnings = rules.check_title("Short Title", min_len=90)
    assert errors == [] and warnings


def test_banned_and_avoided_words():
    errors, _ = rules.check_title("Disney Style Digital Print", banned=["disney"], avoid=["digital"])
    assert any("banned term 'disney'" in e for e in errors)
    assert any("'digital'" in e for e in errors)


def test_terms_match_whole_words_only():
    assert rules.find_terms("Nike Air poster", ["nike"]) == ["nike"]
    assert rules.find_terms("Nikephoros icon", ["nike"]) == []
    assert rules.find_terms("a star wars fan", ["star wars"]) == ["star wars"]


def test_tags_need_exactly_13_short_clean_unique():
    good = [f"tag number {i}" for i in range(13)]
    assert rules.check_tags(good) == []
    assert any("12 tags" in e for e in rules.check_tags(good[:12]))
    assert any("Etsy allows 20" in e for e in rules.check_tags(good[:12] + ["this tag is far too long"]))
    assert any("rejects" in e for e in rules.check_tags(good[:12] + ["bad, tag!"]))
    assert any("duplicate" in e for e in rules.check_tags(good[:12] + ["TAG NUMBER 1"]))


def test_tags_allow_hyphen_and_apostrophe():
    tags = ["mid-century art", "children's print"] + [f"tag {i}" for i in range(11)]
    assert rules.check_tags(tags) == []


def test_sku_rules():
    assert rules.check_sku("art-sun-over-hills-8x10-b") == []
    assert any("32" in e for e in rules.check_sku("a" * 33))
    assert rules.check_sku("Art_Sun")


def test_make_sku_is_unique_and_leaves_room_for_variants():
    taken = {"art-sun-over-hills"}
    sku = rules.make_sku("art", "Sun Over Hills", taken, suffix_len=8)
    assert sku == "art-sun-over-hills-2"
    long = rules.make_sku("art", "An extremely long design name that goes on and on", set(), suffix_len=8)
    assert len(long) + 8 <= rules.SKU_MAX


def test_section_and_option_names():
    assert rules.check_section("Landscape Prints") == []
    assert rules.check_section("A Section Name That Is Too Long")
    assert any("brackets" in e for e in rules.check_option_name("8x10 in (20x25 cm)"))


def test_variant_sku():
    assert rules.variant_sku("art-sun", "A3", "b") == "art-sun-a3-b"
    assert rules.variant_sku("art-sun", "a3", None) == "art-sun-a3"
