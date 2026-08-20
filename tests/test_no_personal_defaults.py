from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN = ("lihaoxu.cn", "chat.lihaoxu.cn", "u.wechat.com/")


def test_framework_sources_have_no_personal_defaults():
    paths = [
        ROOT / "know_me" / "api" / "app.py",
        ROOT / "know_me" / "web_ui" / "index.html",
        ROOT / ".env.example",
    ]
    # resume_dist and resume-site checked in later tasks after move
    bad = []
    for p in paths:
        text = p.read_text(encoding="utf-8")
        for token in FORBIDDEN:
            if token in text:
                bad.append(f"{p}:{token}")
    assert not bad, bad
