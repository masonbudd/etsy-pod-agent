import json

import pytest

from conftest import write_json
from podagent import concepts, research
from podagent.state import Batch


def concept(cid="sun-over-hills", **over):
    c = {"id": cid, "title": "Sun over hills", "target_keyword": "minimalist landscape print",
         "why": "Steady demand.", "prompt": "A sun over hills, flat colours."}
    c.update(over)
    return c


def test_keyword_csv_is_ranked_and_banned_terms_dropped(tmp_path, cfg):
    (tmp_path / "research").mkdir()
    (tmp_path / "research" / "keywords.csv").write_text(
        "Keyword,Searches,Competition,Sales\nsunset wall art,4100,48000,1500\nrare phrase,10,90000,0\n"
        "Disney poster,9000,1000,3000\nsunset wall art,100,100,1\n", encoding="utf-8")
    r = research.gather(tmp_path / "batch", tmp_path, cfg)
    assert [s["phrase"] for s in r["signals"]] == ["sunset wall art", "rare phrase"]
    assert r["dropped_banned"] == ["disney poster"] and r["used"] == ["keyword_csv"]
    assert (tmp_path / "batch" / "research" / "research.json").exists()


def test_profittree_file_keeps_phrases_and_numbers_only(tmp_path, cfg):
    write_json(tmp_path / "research" / "profittree.json", {"keywords": [
        {"phrase": "Boho Sun Print", "searches": "3,300", "competition": 22000, "title": "Someone's listing title", "shop": "x"},
        {"phrase": "https://www.etsy.com/listing/123", "searches": 99},
    ]})
    signals = research.from_profittree(tmp_path / "research" / "profittree.json")
    assert len(signals) == 1 and signals[0].phrase == "boho sun print" and signals[0].searches == 3300
    assert not hasattr(signals[0], "title")


def test_profittree_outranks_csv_on_the_same_phrase(tmp_path, cfg):
    (tmp_path / "research").mkdir()
    (tmp_path / "research" / "keywords.csv").write_text("keyword\nsunset wall art\n", encoding="utf-8")
    write_json(tmp_path / "b" / "research" / "profittree.json", {"keywords": [{"phrase": "sunset wall art", "searches": 4000}]})
    r = research.gather(tmp_path / "b", tmp_path, cfg)
    assert r["signals"][0]["source"] == "profittree"


def test_concepts_are_validated(cfg):
    good = {"concepts": [concept()]}
    research_ = {"signals": [{"phrase": "minimalist landscape print"}]}
    assert concepts.validate(good, cfg, research_, 5) == ([], [])
    bad = {"concepts": [concept(id="Sun Over Hills"), concept(prompt="a Star Wars poster"), concept(why=""),
                        concept(cid="long", prompt="x" * 5000), concept(cid="long")]}
    errors, _ = concepts.validate(bad, cfg, research_, 3)
    text = "\n".join(errors)
    for expected in ("5 concepts", "lowercase words", "banned term 'star wars'", "missing why", "limit is 4000", "duplicate id"):
        assert expected in text
    _, warnings = concepts.validate({"concepts": [concept(target_keyword="unrelated phrase")]}, cfg, research_, 5)
    assert "not in this batch's research" in warnings[0]


def batch_with_concepts(tmp_path, cfg, items):
    b = Batch.create(tmp_path / "b1", "b1", "generate")
    write_json(b.dir / "concepts.json", {"concepts": items})
    concepts.load_into_state(b, cfg, concepts.load_file(b))
    return b


def test_estimates_come_from_config(tmp_path, cfg):
    b = batch_with_concepts(tmp_path, cfg, [concept()])
    c = b.data["concepts"][0]
    assert c["estimate_usd"] == 0.041 and c["worst_case_usd"] == 0.082 and c["decision"] is None


def test_gate1_decisions_and_edits(tmp_path, cfg):
    b = batch_with_concepts(tmp_path, cfg, [concept("a"), concept("b"), concept("c")])
    concepts.decide(b, approve=["a"], reject=["c"], edits={"b": "A better prompt"}, note="ok")
    by = {c["id"]: c for c in b.data["concepts"]}
    assert by["a"]["decision"] == "approved" and by["c"]["decision"] == "rejected"
    assert by["b"]["decision"] == "approved" and by["b"]["edited"] and by["b"]["prompt"] == "A better prompt"
    assert json.loads((b.dir / "concepts.json").read_text())["concepts"][1]["prompt"] == "A better prompt"
    assert concepts.summary(b)["undecided"] == []
    with pytest.raises(KeyError):
        concepts.decide(b, approve=["nope"])


def test_changing_a_prompt_after_approval_clears_the_approval(tmp_path, cfg):
    b = batch_with_concepts(tmp_path, cfg, [concept("a")])
    concepts.decide(b, approve=["a"])
    concepts.load_into_state(b, cfg, {"concepts": [concept("a")]})
    assert b.data["concepts"][0]["decision"] == "approved"
    concepts.load_into_state(b, cfg, {"concepts": [concept("a", prompt="A sneakier prompt")]})
    assert b.data["concepts"][0]["decision"] is None


def test_gate_page_shows_costs_and_budget_warning(tmp_path, cfg):
    cfg["images"]["max_batch_usd"] = 0.05
    b = batch_with_concepts(tmp_path, cfg, [concept("a"), concept("b")])
    concepts.decide(b, approve=["a", "b"])
    page = concepts.write_gate_page(b, cfg, ["a warning"]).read_text(encoding="utf-8")
    assert "Nothing is generated until you approve" in page and "over the batch budget" in page and "a warning" in page
    assert "| **a**" in concepts.markdown_table(b)
