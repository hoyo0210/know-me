# 人设目录示例（脱敏）

本目录为 **可提交的极简模板**（与 `corpus.example/` 同为占位，不含业务叙事）；字段约定见仓库根目录 **README.md** 中「人设（persona）」一节。

## 使用方式

**方式一（推荐）**：复制到本地人设目录（`persona/` 已被 `.gitignore` 排除，不会提交）：

```bash
mkdir -p persona
cp persona.example/IDENTITY.md persona.example/SOUL.md persona/
```

再按需编辑 `persona/*.md` 为真实内容。

**方式二**：不复制，直接指定示例目录（便于快速验证）：

```bash
export KNOW_ME_PERSONA_DIR=persona.example
# 或在 .env 中设置 KNOW_ME_PERSONA_DIR=persona.example
```

正式使用前请将人设放回本地 `persona/`（或你自定义的路径）并改写为真实内容。

## 覆盖目录

环境变量 **`KNOW_ME_PERSONA_DIR`**（绝对或相对路径，会 `resolve`）。未设置时，默认目录为仓库根下的 **`persona/`**（与 `know_me` 包同级；解析逻辑见 `know_me/persona/loader.py`）。若将包装入 `site-packages` 且未设置该变量，请显式设置 `KNOW_ME_PERSONA_DIR`。
