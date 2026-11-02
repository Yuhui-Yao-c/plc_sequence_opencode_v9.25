# CHANGELOG V9.24 — 工作流重构：predict 生成 A档+B档，finish 一次完成

## 核心改动

### 问题
v9.22-v9.23 中 `cmd_a` + `cmd_build` 在 finish 阶段调用，导致：
1. B 档批次在 finish 才生成，agent 处理后必须再跑一次 finish
2. 第一次 finish 产出半成品（空描述交付表）
3. 第二次 finish 重复计算（cmd_a、cmd_build、trace、coverage 全部重跑）

### 修复
将 `cmd_a` + `cmd_build` 从 finish 移到 predict 阶段：

| 阶段 | v9.23（旧） | v9.24（新） |
|---|---|---|
| predict | 候选→原子化→合并→工艺批次 | 候选→原子化→合并→工艺批次 + **cmd_a + cmd_build** |
| agent | 工艺批次处理 | 工艺批次 + B档批次（可并行） |
| finish（第一次） | cmd_a + cmd_build + 验证 + 交付（半成品） | **cmd_validate + 验证 + 交付（成品）** |

### 重构后的流程
```
predict
  ├─ 解析 → 候选 → 原子化 → 合并 → prediction_collection_pairs.csv
  ├─ cmd_a（读 collection）→ action_desc_a.csv
  ├─ cmd_build → batches_desc/*.input.json
  └─ 工艺批次 batches/*.input.json

agent 处理工艺批次 + B档批次（可并行）

finish（一次完成）
  ├─ 验证工艺 → final_prediction_pairs.csv
  ├─ cmd_validate（合并 B+A）→ action_desc_final.csv
  ├─ trace分析
  ├─ 生成交付表（有描述，成品）
  └─ coverage评价
```

## 文件变更

| 文件 | 变更 |
|---|---|
| `action_describe.py` | cmd_a 改读 `prediction_collection_pairs.csv`（fallback 到 final）；删除 `cmd_btier_fallback` 死代码；空 DataFrame 保护 |
| `plc_cli.py` | predict 末尾调用 cmd_a + cmd_build；版本 9.23.0→9.24.0 |
| `workflow.py` | validate 删除 cmd_a/cmd_build 调用，只保留 cmd_validate；版本 9.23→9.24 |

## 验证结果

### 超声波验证（finish 一次完成）
- cmd_a 读 collection（615行）→ A档 194 有描述
- cmd_build 生成 11 批 input.json
- 旧 result.json 行号对齐（collection 与 final 行序 0 不一致）
- finish 直接 cmd_validate 合并：B档 421 / A档 194 / 待补充 0
- 覆盖率 94.1%（与之前一致）
- finish 不再重新跑 cmd_a/cmd_build

### 转接片验证（finish 一次完成）
- cmd_validate 合并：B档 1208 / A档 280 / 待补充 0
- 待核验 0 条
- trace 时长回填 ✓
- 覆盖率 98.7%

### 测试基线
8 failed / 282 passed（无回归）

## 关键设计决策
1. **cmd_a 读 collection 而非 candidate**：candidate 有 2772 行含 641 重复对，collection 是去重后的 1488 行——B 档工作量减少 46%
2. **fallback 到 final**：旧 run（predict 时没有 cmd_a）仍可在 finish 阶段工作
3. **行号对齐**：collection 与 final 行序完全一致（超声 615 行 0 不一致），旧 result.json 无需重新处理
4. **空候选保护**：配对机候选为 0 时 cmd_a 不报错
