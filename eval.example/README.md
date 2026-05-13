# 评测用例示例（脱敏）

- **`cases.sample.jsonl`**：演示 `know-me eval` 所需的 JSONL 格式（`question`、`bucket`、可选 `expect_keywords` 等），问句为占位，**不含个人隐私**。
- **使用**：在仓库根目录执行 `mkdir -p eval && cp eval.example/cases.sample.jsonl eval/cases.jsonl`，再按你的语料改写问句与关键词；`eval/` 已被 `.gitignore` 排除，不会提交。

字段说明见 `know_me/observability/eval_run.py` 中的 `load_cases_jsonl` 与 `run_eval_report`。
