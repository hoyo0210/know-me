# DevOps 手册 — SOP MVP 设计

**日期：** 2026-08-21  
**状态：** 待用户审阅（brainstorm §1–§4 已批准）  
**方法：** Superpowers brainstorming → 本 spec → writing-plans → 分任务执行  

**关联：** Notion [DevOps 全流程实践手册（需求 → 发布）](https://app.notion.com/p/3c2942af317a815e944ac2a08b30e5e8)

---

## 1. 目标与成功标准

在现有 Notion「最佳实践」手册之上，补齐 **可给「你 + Cursor Agent」照做的 SOP MVP**，使执行时不必从长文实践页里抠步骤。

**完成定义（DoD）：**

- [ ] Notion 手册下存在 **SOP 索引** 页 + **SOP-01～04** 四篇（统一模板填满）
- [ ] 手册总览目录导航含 SOP 区入口
- [ ] 实践页 ① / ② / ⑤ 有「执行请用 SOP-0x」交叉链接
- [ ] 仓库存在 `docs/HANDBOOK.md`，仅含 Notion 根链接与 SOP 索引说明（无正文双写）
- [ ] 现有 ①～⑨ 实践内容保留；不删原则，只加链接

**非目标（本里程碑不做）：**

- Deploy / Operate / 事故完整 SOP
- 多角色 RACI、对外培训版、SLA
- Notion ↔ 仓库 SOP 正文双写同步
- 修改 know-me-oss 产品代码或其它业务 Spec

---

## 2. 约束（已拍板）

| 项 | 选择 |
|----|------|
| 受众 | 你 + Cursor Agent（默认执行者）；需人拍板处标「需人确认」 |
| 手册结构 | **混合**：①～⑦ 保留短清单与原则；完整 SOP 独立分区 |
| 范围 | MVP 四篇（见 §4） |
| 权威副本 | **Notion**；仓库只做入口指针 |
| 写法 | **极简 Runbook**（方案 1）：少原理、步骤可勾选 |

---

## 3. 信息架构

```text
📚 DevOps 全流程实践手册（需求 → 发布）   ← 现有根页
├── ①～⑨ …（最佳实践，保留）
└── 📋 SOP 索引（新）
      ├── SOP-01 开 Story / Spec / Plan
      ├── SOP-02 单线 SDD 执行
      ├── SOP-03 开 PR 并合入 main
      └── SOP-04 并行开线（多 Issue / 多 PR）
```

**交叉链接：**

- 总览「目录导航」增加 SOP 索引
- ① Plan → SOP-01；② Code → SOP-02 / SOP-04；⑤ Release → SOP-03
- 各 SOP「相关页」mention 回对应实践页

**仓库：**

- 新增 `docs/HANDBOOK.md`：手册根 URL、SOP 索引说明、四篇标题列表（实现后可填 Notion URL）
- 不复制 SOP 正文

---

## 4. 统一 SOP 模板

每篇固定块顺序（不可缺、不重排）：

1. **何时用** — 1～3 条触发条件  
2. **何时不用** — 边界  
3. **前置** — 可勾选前置条件  
4. **角色** — 默认执行者；「需人确认」点  
5. **步骤** — 编号 + 可勾选；一步一事；含关键 GitHub/命令名  
6. **产出** — 完成时必须存在的工件  
7. **失败升级** — 停手条件与如何问人 / 回实践页  
8. **相关页** — mention 实践页  

**刻意不做：** RACI 大表、截图位、SLA、长篇原理。

---

## 5. 四篇边界与串联

| ID | 名称 | 覆盖 | 不覆盖 |
|----|------|------|--------|
| SOP-01 | 开 Story / Spec / Plan | Issue→DoR→brainstorming→Spec 获批→Plan→Project Ready | 写代码、开分支 |
| SOP-02 | 单线 SDD 执行 | Ready→分支/worktree→Task 串行（含 TDD）→整分支审→可 Draft PR | 多线并行、正式合入 |
| SOP-03 | 开 PR 并合入 main | Ready PR→Checks→审批→Merge→清理→Issue/Project Done | CD 细操；并行协调 |
| SOP-04 | 并行开线 | 检查表→多 Issue/PR→WIP≤2～3→合入顺序→冲突升级 | 单线 Task 内部（用 SOP-02） |

```text
SOP-01 →（每条线）SOP-02 → SOP-03
              ↘ 多线时先 SOP-04，再对每条线套 02→03
```

**内容来源：** 步骤从现有 Notion ①②⑤ 与「SDD 并行 / Repo·Project」章节提炼为可执行清单，不发明与手册冲突的新流程。

---

## 6. 实现顺序（供后续 writing-plans）

1. Notion：建 SOP 索引页（挂在手册根下）  
2. Notion：按模板写 SOP-01～04  
3. Notion：总览 + ①/②/⑤ 交叉链接  
4. 仓库：新增 `docs/HANDBOOK.md` 并填入 Notion URL  
5. 自检：对照本 Spec DoD 勾选  

---

## 7. 风险与缓解

| 风险 | 缓解 |
|------|------|
| SOP 与实践页漂移 | SOP 只写步骤；原理只在实践页；相关页互相 mention |
| Agent 跳过「需人确认」 | 模板中显式标注；失败升级要求停手提问 |
| 仓库入口过时 | `HANDBOOK.md` 只维护少量链接；改 Notion 结构时同步改此文件 |

---

## 8. Spec 自检

- [x] 无「TBD / 稍后补充」占位  
- [x] 与口头批准的 §1–§4 无矛盾  
- [x] 范围可单次 Plan 落地（Notion 页 + 一个 md 指针）  
- [x] 非目标明确，避免扩成全阶段 SOP  
