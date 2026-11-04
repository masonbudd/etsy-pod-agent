import json

import pytest

from podagent import config
from podagent.state import Batch


def test_state_round_trip_and_atomic_save(tmp_path):
    b = Batch.create(tmp_path / "b1", "b1", "inbox")
    b.add_item("sun", "art-sun", "sun.png")
    b.mark("sun", "checks", verdict="pass")
    again = Batch.load(tmp_path / "b1")
    assert again.done("sun", "checks") and again.step("sun", "checks")["verdict"] == "pass"
    assert not list((tmp_path / "b1").glob("*.tmp"))


def test_create_refuses_existing_batch(tmp_path):
    Batch.create(tmp_path / "b1", "b1", "inbox")
    with pytest.raises(FileExistsError):
        Batch.create(tmp_path / "b1", "b1", "inbox")


def test_hold_and_release(tmp_path):
    b = Batch.create(tmp_path / "b1", "b1", "inbox")
    b.add_item("sun", "art-sun", "sun.png")
    b.mark("sun", "checks")
    b.hold("sun", "checks", ["border"], "trim it")
    assert b.held() == ["sun"] and b.active() == []
    b.release("sun", ["checks"])
    assert b.active() == ["sun"] and not b.done("sun", "checks")


def test_unknown_step_is_rejected(tmp_path):
    b = Batch.create(tmp_path / "b1", "b1", "inbox")
    b.add_item("sun", "art-sun", "sun.png")
    with pytest.raises(ValueError):
        b.mark("sun", "publish")


def test_costs_and_runs_are_recorded(tmp_path):
    b = Batch.create(tmp_path / "b1", "b1", "generate")
    b.add_cost("image_generation", 0.063, "sun")
    t0 = b.start_run()
    b.end_run(t0)
    data = json.loads((tmp_path / "b1" / "state.json").read_text())
    assert data["costs"][0]["usd"] == 0.063 and data["runs"][0]["seconds"] is not None


def test_example_config_is_valid(cfg):
    assert config.validate(cfg) == []


def test_validate_catches_bad_settings(cfg):
    cfg["naming"]["sku_prefix"] = "Art_"
    cfg["sizes"][0]["name"] = "8x10 in (20x25 cm)"
    cfg["listing"]["sections"].append("A Section Name That Is Too Long")
    cfg["pricing"]["target_margin"] = 0.95
    problems = "\n".join(config.validate(cfg))
    assert "sku_prefix" in problems and "brackets" in problems and "section" in problems and "pricing" in problems


def test_set_values_edits_one_section_and_keeps_comments(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text('[a]\nshop_id = 0  # keep me\n\n[etsy]\nshop_id = 0  # filled in\nname = "x"\n', encoding="utf-8")
    config.set_values(p, "etsy", {"shop_id": 4567, "name": 'say "hi"'})
    text = p.read_text(encoding="utf-8")
    assert "[a]\nshop_id = 0  # keep me" in text
    assert "shop_id = 4567  # filled in" in text and 'name = "say \\"hi\\""' in text
    with pytest.raises(config.ConfigError):
        config.set_values(p, "etsy", {"missing_key": 1})


def test_parse_ratio():
    assert config.parse_ratio("4:5") == 0.8
    assert config.parse_ratio("ISO") == pytest.approx(0.7071, abs=1e-4)


def test_env_file_is_read(tmp_path, monkeypatch):
    monkeypatch.delenv("ETSY_KEYSTRING", raising=False)
    (tmp_path / ".env").write_text("ETSY_KEYSTRING=abc\n# comment\nOPENAI_API_KEY=\n", encoding="utf-8")
    env = config.load_env(tmp_path)
    assert env == {"ETSY_KEYSTRING": "abc"}
