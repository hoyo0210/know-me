# Know Me OSS Public v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a Public GitHub `know-me` framework (depersonalized, Compose + CI + OSS docs) and a Private `know-me-showcase` for the real resume site, matching `docs/superpowers/specs/2026-08-20-know-me-oss-product-design.md`.

**Architecture:** Dual-repo: framework keeps engine + examples + placeholder `/resume` + neutral assets; showcase receives `resume-site/` and real media. Runtime links via `KNOW_ME_RESUME_BROWSER_URL` (empty default). Compose runs API only; models stay external.

**Tech Stack:** Python 3.10+, FastAPI/uvicorn, Chroma, existing LangChain agent stack, Docker Compose, GitHub Actions, Vue/Vite only inside Private showcase.

## Global Constraints

- Framework must become **Public**; showcase stays **Private**.
- Never commit `.env`, `corpus/`, `persona/`, `data/`, `.product/`, `eval/` (see `.gitignore`).
- No hardcoded `lihaoxu.cn`, `chat.lihaoxu.cn`, real WeChat add URLs, or real-name copy as **runtime defaults**.
- Author demo URLs only in README “Author demo” prose, never as code fallbacks.
- Code-level global rate limiting is **out of scope** (document only; ROADMAP later).
- No bundled LLM image in Compose; no PyPI publish in this milestone.
- Keep eplistudio remote `origin`; add GitHub remote named `github`.
- Suggested names: framework `know-me`, showcase `know-me-showcase` (override if partner provides others).
- Spec DoD: Public + tag `v1.0.0`, CI `/health` smoke, placeholder resume/assets, CONTRIBUTING/SECURITY/CHANGELOG/docs/ROADMAP.md.

---

### Task 1: Test harness + resume URL default guard

**Files:**
- Create: `tests/test_resume_url_defaults.py`
- Create: `tests/conftest.py` (empty or path bootstrap)
- Modify: `pyproject.toml` (optional `[project.optional-dependencies] dev` with `pytest`)
- Modify: `know_me/api/app.py` (`_DEFAULT_RESUME_BROWSER_URL` and `_resume_browser_url`)

**Interfaces:**
- Consumes: `know_me.api.app._resume_browser_url` (or export a small pure helper)
- Produces: `_resume_browser_url()` returns `""` when env unset; never returns `lihaoxu.cn`

- [ ] **Step 1: Add pytest optional dep**

In `pyproject.toml` add:

```toml
[project.optional-dependencies]
dev = ["pytest>=8.0"]
```

- [ ] **Step 2: Write failing test**

```python
# tests/test_resume_url_defaults.py
import os
import importlib

def test_resume_browser_url_default_is_empty(monkeypatch):
    monkeypatch.delenv("KNOW_ME_RESUME_BROWSER_URL", raising=False)
    import know_me.api.app as app
    importlib.reload(app)
    url = app._resume_browser_url()
    assert url == ""
    assert "lihaoxu" not in url.lower()


def test_resume_browser_url_respects_env(monkeypatch):
    monkeypatch.setenv("KNOW_ME_RESUME_BROWSER_URL", "https://example.com/resume")
    import know_me.api.app as app
    importlib.reload(app)
    assert app._resume_browser_url() == "https://example.com/resume"
```

- [ ] **Step 3: Run test — expect FAIL**

```bash
pip install -e ".[dev]"
pytest tests/test_resume_url_defaults.py -v
```

Expected: FAIL because default is still `https://lihaoxu.cn`.

- [ ] **Step 4: Minimal fix in `know_me/api/app.py`**

```python
_DEFAULT_RESUME_BROWSER_URL = ""

def _resume_browser_url() -> str:
    raw = (os.environ.get("KNOW_ME_RESUME_BROWSER_URL") or "").strip()
    return raw
```

Also update `.env.example` comment: resume URL optional, no personal domain example as the only sample — use `https://example.com` if showing a sample.

- [ ] **Step 5: Run tests — expect PASS**

```bash
pytest tests/test_resume_url_defaults.py -v
```

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml tests/ know_me/api/app.py .env.example
git commit -m "fix: make resume browser URL default empty for OSS"
```

---

### Task 2: Depersonalize chat UI resume fallback

**Files:**
- Modify: `know_me/web_ui/index.html` (search `DEFAULT_RESUME_URL` / `lihaoxu`)

**Interfaces:**
- Consumes: injected `%%KNOW_ME_RESUME_URL%%` from server
- Produces: JS fallback is `""` or same-origin only — never `lihaoxu.cn`

- [ ] **Step 1: Write failing grep gate (script test)**

Create `tests/test_no_personal_defaults.py`:

```python
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
```

- [ ] **Step 2: Run — expect FAIL on index.html**

```bash
pytest tests/test_no_personal_defaults.py -v
```

- [ ] **Step 3: Fix `know_me/web_ui/index.html`**

Change:

```javascript
var DEFAULT_RESUME_URL = "https://lihaoxu.cn";
```

to:

```javascript
var DEFAULT_RESUME_URL = "";
```

Ensure any WeChat add URL defaults are empty strings; UI hides QR when unset.

- [ ] **Step 4: Re-run test — PASS**

- [ ] **Step 5: Commit**

```bash
git add know_me/web_ui/index.html tests/test_no_personal_defaults.py
git commit -m "chore: remove personal domain defaults from chat UI"
```

---

### Task 3: Neutral placeholder images for chat UI

**Files:**
- Create: `know_me/web_ui/avatar.png` (neutral placeholder — overwrite if real photo present)
- Create: `know_me/web_ui/wechat_qr.png` (neutral “configure me” placeholder)
- Keep real files out of git history going forward by not committing real binaries

**Interfaces:**
- Consumes: existing UI paths `/avatar.png`, `/wechat_qr.png` (or current static routes)
- Produces: checked-in PNGs that are clearly generic

- [ ] **Step 1: Generate neutral PNGs** (do not reuse real photos)

```bash
python - <<'PY'
from pathlib import Path
try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    import subprocess, sys
    subprocess.check_call([sys.executable, "-m", "pip", "install", "pillow"])
    from PIL import Image, ImageDraw

web = Path("know_me/web_ui")
# avatar: solid + initials KM
img = Image.new("RGB", (256, 256), (40, 44, 52))
d = ImageDraw.Draw(img)
d.ellipse((24, 24, 232, 232), fill=(90, 140, 200))
d.text((88, 108), "KM", fill=(255, 255, 255))
img.save(web / "avatar.png")
# qr placeholder: labeled box
q = Image.new("RGB", (256, 256), (245, 245, 245))
dq = ImageDraw.Draw(q)
dq.rectangle((16, 16, 240, 240), outline=(120, 120, 120), width=4)
dq.text((40, 118), "QR placeholder", fill=(80, 80, 80))
q.save(web / "wechat_qr.png")
print("ok")
PY
```

If real photos were untracked, replace them with these before `git add`.

- [ ] **Step 2: Confirm UI still references same filenames** (grep `avatar.png` / `wechat_qr.png` in `index.html`).

- [ ] **Step 3: Commit**

```bash
git add know_me/web_ui/avatar.png know_me/web_ui/wechat_qr.png
git commit -m "chore: add neutral avatar and QR placeholders"
```

---

### Task 4: Placeholder `/resume` theme (no real identity)

**Files:**
- Modify or rebuild: `know_me/web_ui/resume_dist/` (placeholder static build)
- Optionally keep a minimal `examples/placeholder-resume/` later — for v1, replace `resume_dist` content only
- Ensure `know_me/api/app.py` `/resume` still serves `resume_dist`

**Interfaces:**
- Consumes: `_RESUME_DIST_DIR` in `app.py`
- Produces: HTML title/body without 李昊旭 / lihaoxu

- [ ] **Step 1: Extend personal-defaults test** to scan `know_me/web_ui/resume_dist/**/*.html` for `lihaoxu` and `李昊旭`.

- [ ] **Step 2: Replace resume_dist with minimal placeholder**

Create `know_me/web_ui/resume_dist/index.html`:

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Know Me — Placeholder Resume</title>
  <style>
    body { font-family: system-ui, sans-serif; max-width: 40rem; margin: 3rem auto; padding: 0 1rem; color: #222; }
    .hint { color: #666; }
  </style>
</head>
<body>
  <h1>Your Name</h1>
  <p class="hint">Placeholder resume page shipped with Know Me. Replace via KNOW_ME_RESUME_BROWSER_URL or build your own static site in a separate showcase repo.</p>
  <p><a href="../">Back to chat</a></p>
</body>
</html>
```

Remove old hashed assets under `resume_dist/assets/` if they embed personal copy (delete directory contents no longer referenced).

- [ ] **Step 3: Run personal-defaults test including resume_dist — PASS**

- [ ] **Step 4: Commit**

```bash
git add know_me/web_ui/resume_dist tests/test_no_personal_defaults.py
git commit -m "chore: ship placeholder /resume without personal identity"
```

---

### Task 5: Move `resume-site/` out of framework tree

**Files:**
- Delete from framework working tree after copy: `resume-site/`
- Create sibling or documented path for showcase init (partner machine): e.g. export tarball

**Interfaces:**
- Produces: framework tree without `resume-site/`; instructions in README for showcase

- [ ] **Step 1: Archive current resume-site for showcase**

```bash
# from repo root — keep a local backup outside git if needed
mkdir -p ../know-me-showcase-export
rsync -a resume-site/ ../know-me-showcase-export/resume-site/
# also copy real assets if any were only under web_ui before overwrite — from backup if kept
```

- [ ] **Step 2: Remove `resume-site/` from this repo**

```bash
git rm -r --cached resume-site 2>/dev/null || true
rm -rf resume-site
```

If never tracked, just `rm -rf resume-site` and ensure it is not added.

- [ ] **Step 3: Add note to `.gitignore` optional** — not required if directory gone.

- [ ] **Step 4: Commit framework removal**

```bash
git add -A
git status  # confirm resume-site gone, no secrets
git commit -m "chore: remove resume-site from framework repo (moved to showcase)"
```

- [ ] **Step 5: Initialize Private showcase repo** (partner provides GitHub auth)

```bash
cd ../know-me-showcase-export
git init
# add README explaining Private showcase + VITE_CHAT_URL
git add resume-site
git commit -m "feat: import personal resume-site showcase"
# create Private repo know-me-showcase on GitHub, then:
# git remote add origin git@github.com:<USER>/know-me-showcase.git
# git push -u origin main
```

Do **not** block framework Public on showcase push if partner delays — but framework must not contain real site.

---

### Task 6: Commit remaining engine WIP (safe files only)

**Files:**
- Modify/add: existing dirty `know_me/**` agent/rag/cli modules listed in `git status`
- Do **not** add real corpus/persona/data

**Interfaces:**
- Produces: buildable `pip install -e .` with current Agent features on `main`/feature branch

- [ ] **Step 1: Review `git status` / `git diff` for secrets**

Forbidden to stage: `.env`, `*.sqlite`, anything under `corpus/`, `persona/`, `data/`.

- [ ] **Step 2: Stage and commit engine features**

```bash
git add know_me/ .env.example persona.example/IDENTITY.md pyproject.toml
git commit -m "feat: land agent session, SQLite, and RAG enhancements"
```

Split into two commits if diff is huge (agent vs rag) — preferred for review.

---

### Task 7: Docker Compose application layer

**Files:**
- Create: `Dockerfile`
- Create: `docker-compose.yml`
- Create: `.dockerignore`
- Modify: `README.md` Quickstart section

**Interfaces:**
- Produces: `docker compose up` serving HTTP on `8000`; env for OpenAI-compatible base URL

- [ ] **Step 1: Dockerfile**

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY know_me ./know_me
COPY corpus.example ./corpus.example
COPY persona.example ./persona.example
RUN pip install --no-cache-dir -e .
ENV KNOW_ME_CORPUS_ROOT=corpus.example
ENV KNOW_ME_PERSONA_DIR=persona.example
EXPOSE 8000
CMD ["know-me", "serve", "--host", "0.0.0.0", "--port", "8000"]
```

- [ ] **Step 2: docker-compose.yml**

```yaml
services:
  know-me:
    build: .
    ports:
      - "8000:8000"
    environment:
      KNOW_ME_OPENAI_BASE_URL: ${KNOW_ME_OPENAI_BASE_URL:-http://host.docker.internal:1234/v1}
      KNOW_ME_OPENAI_EMBED_MODEL: ${KNOW_ME_OPENAI_EMBED_MODEL:-}
      KNOW_ME_OPENAI_CHAT_MODEL: ${KNOW_ME_OPENAI_CHAT_MODEL:-}
      KNOW_ME_OPENAI_API_KEY: ${KNOW_ME_OPENAI_API_KEY:-}
      KNOW_ME_CORPUS_ROOT: corpus.example
      KNOW_ME_PERSONA_DIR: persona.example
    extra_hosts:
      - "host.docker.internal:host-gateway"
```

- [ ] **Step 3: .dockerignore**

```
.venv
data
corpus
persona
eval
.git
.product
**/__pycache__
resume-site
```

- [ ] **Step 4: Manual smoke**

```bash
docker compose build
docker compose up -d
curl -sf http://127.0.0.1:8000/health
docker compose down
```

Expected: HTTP 200 JSON from `/health` (even if models unset — confirm current `/health` behavior; if it requires models, adjust health to be liveness-only without model check).

- [ ] **Step 5: Commit**

```bash
git add Dockerfile docker-compose.yml .dockerignore README.md
git commit -m "feat: add Docker Compose quickstart for app layer"
```

---

### Task 8: GitHub Actions CI health smoke

**Files:**
- Create: `.github/workflows/ci.yml`

**Interfaces:**
- Produces: CI green on push/PR

- [ ] **Step 1: Workflow**

```yaml
name: ci
on:
  push:
  pull_request:
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install -e ".[dev]"
      - run: pytest -q
  image-health:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: docker compose build
      - run: docker compose up -d
      - run: |
          for i in $(seq 1 30); do
            curl -sf http://127.0.0.1:8000/health && exit 0
            sleep 2
          done
          exit 1
      - if: always()
        run: docker compose down
```

- [ ] **Step 2: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: pytest and compose /health smoke"
```

---

### Task 9: OSS facade docs

**Files:**
- Create: `CONTRIBUTING.md`
- Create: `SECURITY.md`
- Create: `CHANGELOG.md`
- Create: `DISCLAIMER.md` (or README section — prefer `DISCLAIMER.md`)
- Create: `docs/ROADMAP.md`
- Modify: `README.md` (What/Why/Quickstart/Architecture/Security notes/Author demo link)

**Interfaces:**
- Produces: files required by spec DoD

- [ ] **Step 1: Write CONTRIBUTING.md** — setup, `pip install -e ".[dev]"`, pytest, PR expectations, no real corpus in PRs.

- [ ] **Step 2: Write SECURITY.md** — report vulns via GitHub private advisory; warn that public `/chat` needs reverse-proxy rate limits/WAF; ingest key required for `/ingest`.

- [ ] **Step 3: Write CHANGELOG.md** — `[1.0.0]` Unreleased/OSS first Public release notes once tagged.

- [ ] **Step 4: Write DISCLAIMER.md** — not employment/salary commitment; answers may be incomplete; HR screening reference only.

- [ ] **Step 5: Write docs/ROADMAP.md** — sanitized: hybrid retrieval, rate limit middleware, PyPI, Milvus — no private PRD text.

- [ ] **Step 6: Rewrite README Quickstart** for Compose + example corpus; document `KNOW_ME_RESUME_BROWSER_URL`; Author demo optional URL in prose only.

- [ ] **Step 7: Commit**

```bash
git add CONTRIBUTING.md SECURITY.md CHANGELOG.md DISCLAIMER.md docs/ROADMAP.md README.md
git commit -m "docs: add OSS contributing, security, roadmap, and disclaimer"
```

---

### Task 10: pyproject package metadata + package-data

**Files:**
- Modify: `pyproject.toml`

**Interfaces:**
- Produces: package includes `web_ui` html, png, resume_dist; project URLs placeholder until Public

- [ ] **Step 1: Update package-data**

```toml
[tool.setuptools.package-data]
know_me = [
  "web_ui/*.html",
  "web_ui/*.png",
  "web_ui/resume_dist/**/*",
]
```

Add:

```toml
[project.urls]
Homepage = "https://github.com/<USER>/know-me"
Issues = "https://github.com/<USER>/know-me/issues"
```

(Replace `<USER>` when known.)

Bump version to `1.0.0` when cutting release (can be this commit or Task 12).

- [ ] **Step 2: Commit**

```bash
git add pyproject.toml
git commit -m "chore: package web UI assets and prepare 1.0 metadata"
```

---

### Task 11: Pre-Public scan + push Private GitHub

**Files:** none (ops)

- [ ] **Step 1: Scan**

```bash
rg -n "lihaoxu|u\\.wechat\\.com|李昊旭" --glob '!.git/**' --glob '!docs/superpowers/**' || true
# Expect: no hits in know_me/, .env.example, Dockerfile, README defaults
# README Author demo line may mention live site URL in prose — allowed if not a code default
```

- [ ] **Step 2: Partner creates empty Private repo `know-me` (no README)**

- [ ] **Step 3: Add remote and push**

```bash
git remote add github https://github.com/<USER>/know-me.git
git push -u github main
```

- [ ] **Step 4: Verify on GitHub** — Private; no `.env`/`corpus/`; CI starts.

---

### Task 12: Tag v1.0.0 and switch Public

**Files:**
- Modify: `CHANGELOG.md` date; `know_me/__init__.py` / `pyproject.toml` version `1.0.0`

- [ ] **Step 1: Version bump commit if not done**

```bash
# set version 1.0.0 in pyproject.toml and know_me/__init__.py
git add pyproject.toml know_me/__init__.py CHANGELOG.md
git commit -m "chore: release 1.0.0"
git tag -a v1.0.0 -m "Know Me 1.0.0 open-source framework"
git push github main --tags
```

- [ ] **Step 2: GitHub Settings → Change repository visibility to Public**

- [ ] **Step 3: Create GitHub Release from tag `v1.0.0`**

- [ ] **Step 4: Final DoD checklist** from spec §9 — all boxes checked.

- [ ] **Step 5: Announce finishing-a-development-branch options** to partner (merge/PR/keep).

---

## Spec coverage self-check

| Spec requirement | Task |
|------------------|------|
| Empty/non-personal resume URL default | 1–2 |
| Neutral avatar/QR | 3 |
| Placeholder `/resume` | 4 |
| Remove `resume-site` / Private showcase | 5 |
| Engine land | 6 |
| Compose + external model | 7 |
| CI `/health` | 8 |
| CONTRIBUTING/SECURITY/CHANGELOG/ROADMAP/DISCLAIMER | 9 |
| Package data | 10 |
| GitHub Private then Public + tag | 11–12 |
| No code rate limit (docs only) | 9 SECURITY/ROADMAP |
| Keep eplistudio origin | 11 (add `github` only) |

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-08-20-know-me-oss-v1.md`.

**Two execution options:**

1. **Subagent-Driven (recommended)** — fresh subagent per task, review between tasks  
2. **Inline Execution** — `executing-plans` in this session with checkpoints  

Which approach?
