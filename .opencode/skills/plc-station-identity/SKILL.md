# Skill: plc-station-identity

# 工位身份映射（predict 阶段建立，展示层只读）

## 何时加载
predict 完成后、生成交付表前；或排查"工位名裸/无中文/排序混乱"问题时加载。

## 业务目标
从"展示层猜名字 + 逐个工程加正则"转变为"predict 阶段建身份 + 展示层只读 + 无文档进 review"。
工位身份 = 状态变量根结构体的源码文档（变量声明 + documentation），不依赖 POU 命名约定或正则猜测。

## 身份键规则
- **身份键 = 变量根完整名**（如 `udt_EM04`、`HJXHX_ST08`），不剥离命名空间前缀。
  `HJXHX_ST08`（铝焊线）和 `ST08`（滚筒线）是**不同工位**，剥离前缀会塌缩身份。
- **显示编号剥离**仅限 IEC61131-3 标准类型前缀（`udt_`/`st`/`St`/`ST` 后跟大写字母）；
  命名空间前缀（`HJXHX_`/`TJXHX_`/`AlWeld_`/`CuWeld_` 等）保留。
- **排序键**从变量根最后一个数字组提取 `[主号, 子号]`（仅排序用，不影响显示/身份）。

## 产物
predict 自动生成 run 目录 `station_identity.json`：
```json
{
  "udt_EM04": {
    "display": "EM04_长边清洗工位",
    "code": "EM04",
    "doc": "长边清洗工位",
    "sort_key": [4, 0],
    "source": "variable_documentation",
    "needs_review": false
  }
}
```
- `candidate_audit.json` 增加 `station_identity` 审计段：total_var_roots / with_documentation / without_documentation / without_documentation_roots。
- 无文档的变量根 `needs_review=true`，`display=变量根本身`，**predict 完成后 stderr 预警**，不等交付表才发现。

## 数据来源
1. `prediction_candidate_pairs.csv` 的"控制器"列 → 提取变量根（`.` 前）。
   只处理**实际作为状态机锚点**的变量，辅助数据结构（如 `HJXHX_ST08_Data`）不建立身份。
2. `variable_documentation.json` 的 stations + all → 查文档。
   stations 是 type 名含 'station' 的子集，all 是全量 fallback。

## 展示层规则（gen_workstation_xlsx.py）
1. 优先读 `station_identity.json` 的 `display` → 直接用工位粒度名（如 `EM04_长边清洗工位`）。
2. 构成要素 SC04 中文后缀是**子工位粒度细化**（如 `ST06_铜上盘` vs `ST06_铜侧换盘`），
   用 station_identity 的 **code** + 子工位名，身份不丢失。
3. `station_identity.json` 不存在时 fallback 到 v9.16 var_docs 路径（零退化）。
4. 排序优先用 station_identity 的 `sort_key`，无映射时用 `_em_sort_key`。

## 红线
- 不逐个工程加正则前缀（`udt_EM`/`HJXHX_ST`/`fbEM`…）；标准类型前缀剥离是唯一通用规则。
- 不剥离命名空间前缀作为身份键；`HJXHX_ST08` ≠ `ST08`。
- 无文档不静默用裸名；进 review + stderr 预警。
- station_identity 是 predict 产物，展示层只读不改。
- 子工位粒度（构成要素后缀）是展示增强，不替代主工位身份。

Base directory: E:\users\yaoyuhui\步序采集汇总\工程优化\plc-sequence-opencode-v9.13\.opencode\skills\plc-station-identity
