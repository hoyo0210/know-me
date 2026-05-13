# 语料目录示例（脱敏）

本目录为 **可提交的占位结构**，用于对照 `know_me/loaders.py` 的目录约定；正文不含真实个人信息。

## 使用方式

**方式一（推荐）**：复制四类子目录到本地语料根目录（不会复制本说明文件）：

```bash
mkdir -p corpus
cp -R corpus.example/about_me corpus.example/faq corpus.example/hr_faq corpus.example/hr_screening corpus/
```

再按需编辑 `corpus/**/*.md` 或增删文件；`corpus/` 已被 `.gitignore` 排除，不会提交。

**方式二**：不复制，直接对示例目录建索引（便于快速验证管道）：

```bash
know-me build-index --corpus-root corpus.example
```

正式使用前请将语料放回本地 `corpus/` 并改写为真实内容。

## 字段说明

Markdown 与可选 YAML front matter 见仓库根目录 **README.md** 中「语料库（corpus）」一节；元数据合并逻辑见 `know_me/index/splitting.py` 的 `build_chunk_metadata`。
