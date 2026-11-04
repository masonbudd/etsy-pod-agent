import importlib.util

from PIL import Image, PngImagePlugin

from podagent import cli
from podagent.config import REPO

spec = importlib.util.spec_from_file_location("scan_secrets", REPO / "scripts" / "scan_secrets.py")
scan = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scan)


def scan_text(tmp_path, monkeypatch, name, text, deny=""):
    monkeypatch.setattr(scan, "ROOT", tmp_path)
    if deny:
        (tmp_path / ".scan-denylist").write_text(deny, encoding="utf-8")
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return scan.scan_file(p, scan.load_denylist())


def test_scanner_finds_keys_tokens_and_emails(tmp_path, monkeypatch):
    fake_key = "sk-" + "a1B2" * 8
    email = "someone" + "@gmail.com"
    hits = scan_text(tmp_path, monkeypatch, "x.py", f'key = "{fake_key}"\nemail me at {email}\n')
    kinds = " ".join(hits)
    assert "OpenAI key" in kinds and "email address" in kinds
    assert fake_key not in kinds                                  # findings are masked


def test_scanner_allows_safe_emails_and_placeholders(tmp_path, monkeypatch):
    assert scan_text(tmp_path, monkeypatch, "x.md", "write to noreply@example.com\nETSY_KEYSTRING=\n") == []


def test_scanner_flags_home_paths_but_not_api_paths(tmp_path, monkeypatch):
    assert scan_text(tmp_path, monkeypatch, "a.md", "see C:" + "\\Users\\alice\\shop")
    assert scan_text(tmp_path, monkeypatch, "b.md", "GET /v3/application/users/me") == []


def test_scanner_blocks_secret_files(tmp_path, monkeypatch):
    assert scan_text(tmp_path, monkeypatch, ".env", "A=1")
    assert scan_text(tmp_path, monkeypatch, ".env.example", "A=") == []


def test_private_denylist_with_allowed_path(tmp_path, monkeypatch):
    deny = "Acme Prints\nRivalCo @ docs/*.md\n"
    assert scan_text(tmp_path, monkeypatch, "a.py", "made for Acme Prints", deny)
    (tmp_path / "docs").mkdir()
    assert scan_text(tmp_path, monkeypatch, "docs/b.md", "tested on RivalCo", deny) == []


def test_scanner_reads_image_metadata(tmp_path, monkeypatch):
    monkeypatch.setattr(scan, "ROOT", tmp_path)
    info = PngImagePlugin.PngInfo()
    info.add_text("Author", "someone" + "@gmail.com")
    Image.new("RGB", (4, 4)).save(tmp_path / "a.png", pnginfo=info)
    assert scan.scan_file(tmp_path / "a.png", [])


def test_cli_init_then_new_batch(tmp_path, capsys):
    assert cli.main(["--root", str(tmp_path), "init"]) == 0
    assert (tmp_path / "config.toml").exists() and (tmp_path / "rooms" / "corners.json").exists()
    Image.new("RGB", (512, 768), (200, 120, 90)).save(tmp_path / "designs" / "inbox" / "A Design.png")
    assert cli.main(["--root", str(tmp_path), "new"]) == 0
    assert '"a-design": "art-a-design"' in capsys.readouterr().out


def test_cli_reports_missing_config(tmp_path, capsys):
    assert cli.main(["--root", str(tmp_path), "doctor"]) == 1
    assert "podagent init" in capsys.readouterr().err
