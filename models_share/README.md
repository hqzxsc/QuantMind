# 冒烟测试模型共享包（13 种模型类型）

一次性共享包：2026-08-25 端到端验证训练出的 **13 个模型**（lightgbm / xgboost /
catboost / random_forest / linear / mlp / gru / lstm / alstm / transformer /
tabnet / tcn / nativetft），供其他环境/用户拉取后注册测试。

## 使用步骤

```bash
# 1) 拉代码后解压模型包（包内结构 models/users/<tenant>/<user>/mdl_cn_train_*）
mkdir -p models && tar xzf models_share/smoke_13_models_20260825.tar.gz -C models

# 2) 注册到自己的账号（user_id 是 users 表主键，如 admin 账号为 "00000001"）
#    预览：
python scripts/register_models_from_dir.py --user-id 00000001 --dry-run
#    执行：
python scripts/register_models_from_dir.py --user-id 00000001

# 3) OSS 单容器部署（models 根为 /app/models，env 已注入）
docker cp models/smoke_13_models_20260825.tar.gz quantmind:/tmp/
docker exec quantmind sh -c 'mkdir -p /app/models && tar xzf /tmp/smoke_13_models_20260825.tar.gz -C /app/models'
docker exec quantmind python3 -c "
  import sys; sys.path.insert(0, '/app/scripts')
  import register_models_from_dir as m; m.main(['--user-id', '00000001'])"
```

注册后主栏「模型管理」即可看到（按登录用户过滤，注册到哪个 user_id 就哪个账号可见）。

## 说明

- 包内**不含** pred.pkl / pred.parquet 预测产物（单个模型 80MB 的推理缓存，git 不承载），
  模型文件 + metadata.json + config.yaml + result.json 均已包含，可直接推理/回测
- 质量门禁与线上一致：`test_rank_icir >= 0.05 且 test_rank_ic > 0` → ready，
  否则 candidate（CatBoost / Linear / NativeTFT 三只是 candidate，仍可推理）
- 注册脚本规则与训练完成回调（`model_registry.register_model_from_training_run`）一致：
  metadata_json 组装、metrics 平铺、不设 is_default（避免撞每用户唯一默认约束）
- 模型目录归属：`qm_user_models.user_id` = 注册时指定的 `--user-id`，与主栏过滤一致

## 模型清单（13 个，2026-08-25 训练）

| model_type | 状态 | test_rank_icir | 说明 |
|---|---|---|---|
| lightgbm | ready | 0.157 | |
| xgboost | ready | - | |
| random_forest | ready | - | |
| mlp | ready | - | |
| gru | ready | - | |
| lstm | ready | - | |
| alstm | ready | - | |
| transformer | ready | - | |
| tabnet | ready | - | |
| tcn | ready | - | |
| catboost | candidate | 0.049 | 门禁未过 |
| linear | candidate | - | 门禁未过 |
| nativetft | candidate | - | 输出退化常数 |
