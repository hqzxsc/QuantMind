# docker/training

用途：训练容器内的模型训练入口脚本与运行时辅助文件。

## 说明
- 统一运行镜像为 `quantmind-ml-runtime:latest`。
- `train.py` 会在用户提交特征的基础上自动补齐 6 个基础特征：`mom_ret_1d`、`mom_ret_5d`、`mom_ret_20d`、`liq_volume`、`liq_amount`、`fun_turnover_1`。
- `metadata.json` 现在会同时记录三层口径：
  - `requested_feature_count/requested_features`：前端提交的特征
  - `auto_appended_feature_count/auto_appended_features`：训练脚本自动补齐的基础特征
  - `feature_count/features/feature_columns`：最终实际入模特征
- 训练结束后默认生成 `shap_summary.csv`，使用 LightGBM 原生 `pred_contrib=True` 计算 SHAP 汇总贡献度；默认读取验证集、采样 30000 行，并在 `metadata.json.shap` 中记录状态、样本数、耗时和错误信息。SHAP 失败不会阻断训练完成。
- 前端训练页会据此展示“提交特征数 / 自动补充特征 / 实际入模特征数”，便于排查维度不一致问题。

## 因子筛选流水线（data/factor_selection.py）

IC/ICIR 初筛 → 自适应回填 → 相关性 + 多样性剪枝 → 稳定性检验 → PFS 扰动保真度。
后两道质量闸门对齐 AlphaEval（KDD 2026，arXiv 2508.13174）的五维评估，纯函数在
`data/factor_quality.py`（无容器依赖，engine 侧经 `backend/shared/factor_quality.py`
按路径加载同一份实现，**改公式只需改这一个文件**）：

- **DH 多样性增益**（`dh_enabled` / `dh_min_gain`，默认开 / 0.1）：两两 |ρ| 阈值之外，
  要求新增因子给集合带来的有效因子数（`N_eff = exp(特征值熵)`）增量不低于阈值——
  拦「与多只同时中等相关」的多重共线（两两阈值是盲的）。收紧到 0.3+ 会明显减少特征数。
- **PFS 扰动保真度**（`pfs_enabled` / `pfs_threshold`，默认开 / 0.9）：截面 z 分加
  高斯与 t 厚尾噪声，逐日算排名保真度取最差；排名一扰就塌的因子（并值/离散型居多）
  换成后续候选。淘汰后幸存者 < 30 视为判定过严，回退原名单并在报告标 `pfs_fallback`。
- 报告新增键（`features[].pfs/dh_gain/pfs_backfilled`、`diversity`、`stage_counts.pfs_pass`、
  `thresholds.pfs_threshold`）均为**只增键**，老前端不受影响；训练结果页漏斗已加 PFS 一级。
- 实测（429 因子库 × 60 交易日，n_top=80）：相关性+多样性淘汰 25 个、PFS 换掉 15 个，
  入选集合从 73 个降到 54 个但 N_eff 只降 3.7（29.4→25.7，且剔除的 12 个里
  有 PFS=0.37 的极端脆弱因子）；整条流水线耗时 +25%（71.6s → 89.7s）。
- 关掉两道闸门（`pfs_enabled=false` + `dh_enabled=false`）与老行为逐行一致。

## 多核 / 多线程控制

树模型与因子筛选默认用满机器所有 CPU 核心，可通过环境变量限制：

| 环境变量 | 默认 | 说明 |
|----------|------|------|
| `TRAIN_IC_WORKERS` | min(CPU 核数, 特征数, 交易日数) | 因子筛选（日频 Rank IC 计算）的并行进程数；设 `0` 或 `1` 退化为串行 |
| `TRAIN_NTHREADS` | `-1`（全部核心） | 各树模型框架（LightGBM/XGBoost/CatBoost/RandomForest）的线程数 |

- 因子筛选原为单核嵌套循环（2026 单年快照实测 79 秒，全量多年训练集估约 15-20 分钟），现改为多进程并行
  （`parallel_utils.py`，fork 共享内存零拷贝），**数值与串行版逐日 spearmanr 完全一致**。
- 并行按**交易日分块**（而非按特征分块）：父进程一次性 `groupby('trade_date')` 后，
  把日期区间切成 N 段分发给各 worker，每行数据只被一个 worker 读取——内存带宽真正分摊，
  20 核实测 7.9×（按特征分块时每个 worker 都要扫全表，16 worker 反而慢于 8 worker）。
- LightGBM 原生 API 的线程参数名是 `num_threads`（`n_jobs` 是 sklearn 层别名，
  直接传给 `lgb.train()` 会被忽略导致多核不生效），`train.py` 已修正并透传 `TRAIN_NTHREADS`。
- 多模型/OOF/DeepLearning 同时训练时内存叠加易 OOM，内存紧张时可
  设置 `TRAIN_NTHREADS=4` 限制单个模型线程数。
- 编排器（本地 Docker / 远端 AutoDL）会自动挂载/同步 `parallel_utils.py` 与 `train.py` 同目录。
