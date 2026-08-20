from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN = ("lihaoxu.cn", "chat.lihaoxu.cn", "u.wechat.com/")
RESUME_FORBIDDEN = ("lihaoxu", "李昊旭")


def test_framework_sources_have_no_personal_defaults():
    paths = [
        ROOT / "know_me" / "api" / "app.py",
        ROOT / "know_me" / "web_ui" / "index.html",
        ROOT / ".env.example",
    ]
    bad = []
    for p in paths:
        text = p.read_text(encoding="utf-8")
        for token in FORBIDDEN:
            if token in text:
                bad.append(f"{p}:{token}")
    assert not bad, bad


def test_resume_dist_has_no_personal_identity():
    resume_dir = ROOT / "know_me" / "web_ui" / "resume_dist"
    bad = []
    for html_path in resume_dir.rglob("*.html"):
        text = html_path.read_text(encoding="utf-8")
        for token in RESUME_FORBIDDEN:
            if token in text:
                bad.append(f"{html_path}:{token}")
    assert not bad, bad
