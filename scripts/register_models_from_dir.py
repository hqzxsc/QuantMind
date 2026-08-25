#!/usr/bin/env python3
"""从模型目录批量注册模型到 qm_user_models（本地注册脚本）。

用途：把共享/导入的模型目录（如 models_share 里的 13 个冒烟模型包解压后的
目录）注册到当前 QuantMind 实例的 qm_user_models 表，注册到指定用户名下，
供主栏"模型管理" / 推理 / 回测使用。

与训练完成回调（model_registry.register_model_from_training_run）保持一致的
注册规则：
  - metadata_json 组装（context/market/display_name/model_name/...）
  - metrics_json 直接用 metadata.json 里的 metrics 平铺字段
  - 质量门禁：test_rank_icir >= 0.05 且 test_rank_ic > 0 → ready，否则 candidate
  - 不设 is_default（避免撞 uq_qm_user_models_default_per_user 唯一约束）

用法：
  # 1) 解压模型包（包内是 models/users/<tenant>/<user>/mdl_cn_train_*）
  mkdir -p models && tar xzf models_share/smoke_13_models_20260825.tar.gz -C models

  # 2) 注册到指定用户（user_id 是 users 表主键，如 admin 账号的 "00000001"）
  python scripts/register_models_from_dir.py --user-id 00000001 --dry-run   # 预览
  python scripts/register_models_from_dir.py --user-id 00000001             # 执行

  # 容器内执行（OSS 部署，models 根 /app/models，env 已注入）
  docker cp models/smoke_13_models_20260825.tar.gz quantmind:/tmp/
  docker exec quantmind python3 -c "
    import sys; sys.path.insert(0, '/app/scripts')
    import register_models_from_dir as m; m.main(['--user-id', '00000001'])"

数据库连接走环境变量 DB_HOST/DB_PORT/DB_NAME/DB_USER/DB_PASSWORD（.env 或容器 env）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass


def _db_connect():
    import psycopg2

    return psycopg2.connect(
        host=os.environ.get("DB_HOST", "localhost"),
        port=int(os.environ.get("DB_PORT", "5432")),
        dbname=os.environ.get("DB_NAME", "quantmind"),
        user=os.environ.get("DB_USER", "quantmind"),
        password=os.environ.get("DB_PASSWORD", ""),
    )


# 与 model_registry._sync_candidate_artifacts 保持一致的模型文件优先级
MODEL_FILE_NAMES = [
    "model.lgb",
    "model.xgb",
    "model.cbm",
    "model.pkl",
    "model.pth",
    "model.onnx",
    "model.pt",
]


def find_model_dirs(models_root: Path):
    """扫描 {root}/users/{tenant}/{user}/mdl_cn_*/ 含 metadata.json 的目录。"""
    found = []
    users_dir = models_root / "users"
    if not users_dir.is_dir():
        return found
    for tenant_dir in sorted(users_dir.iterdir()):
        if not tenant_dir.is_dir():
            continue
        for user_dir in sorted(tenant_dir.iterdir()):
            if not user_dir.is_dir():
                continue
            for model_dir in sorted(user_dir.iterdir()):
                if not model_dir.is_dir():
                    continue
                if (model_dir / "metadata.json").is_file():
                    found.append(model_dir)
    return found


def resolve_model_file(model_dir: Path) -> str:
    for name in MODEL_FILE_NAMES:
        if (model_dir / name).is_file():
            return name
    return ""


def quality_gate_status(metadata: dict) -> str:
    """复刻 model_registry 的软门禁：test_rank_icir>=0.05 且 test_rank_ic>0 → ready。"""
    metrics = metadata.get("metrics") or {}
    icir = metrics.get("test_rank_icir")
    ic = metrics.get("test_rank_ic")
    if isinstance(icir, (int, float)) and isinstance(ic, (int, float)):
        if icir >= 0.05 and ic > 0:
            return "ready"
    return "candidate"


def build_metadata_json(metadata: dict, model_dir: Path) -> dict:
    """与 register_model_from_training_run 的 metadata 组装保持一致。"""
    req_context = metadata.get("context") if isinstance(metadata.get("context"), dict) else {}
    market_str = str(req_context.get("market") or metadata.get("market") or "CN").upper().strip()
    merged_context = {**metadata.get("context", {}), **req_context}
    if not str(merged_context.get("market") or "").strip():
        merged_context["market"] = market_str
    raw_display_name = str(
        metadata.get("display_name")
        or metadata.get("job_name")
        or metadata.get("model_name")
        or model_dir.name
    )
    if market_str and not raw_display_name.upper().endswith(f"_{market_str}"):
        raw_display_name = f"{raw_display_name}_{market_str}"
    return {
        **metadata,
        "context": merged_context,
        "market": market_str or "CN",
        "display_name": raw_display_name,
        "model_name": str(metadata.get("model_name") or metadata.get("job_name") or model_dir.name),
        "target_horizon_days": metadata.get("target_horizon_days"),
        "target_mode": metadata.get("target_mode"),
        "label_formula": metadata.get("label_formula"),
        "training_window": metadata.get("training_window"),
    }


def register(conn, *, tenant_id: str, user_id: str, model_dir: Path, force: bool = False) -> str:
    metadata = json.loads((model_dir / "metadata.json").read_text(encoding="utf-8"))
    model_id = metadata.get("model_id") or model_dir.name
    model_file = resolve_model_file(model_dir)
    metrics = metadata.get("metrics") or {}
    status = quality_gate_status(metadata)
    storage_path = str(model_dir.resolve())

    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM qm_user_models WHERE tenant_id=%s AND user_id=%s AND model_id=%s",
            (tenant_id, user_id, model_id),
        )
        exists = cur.fetchone() is not None
        if exists and not force:
            return f"SKIP  {model_id} (已存在, --force 可覆盖)"

        metadata_json = build_metadata_json(metadata, model_dir)
        import datetime

        now = datetime.datetime.now(datetime.timezone.utc)
        if exists:
            cur.execute(
                """
                UPDATE qm_user_models SET
                    status=%s, storage_path=%s, model_file=%s,
                    metadata_json=%s, metrics_json=%s, updated_at=%s
                WHERE tenant_id=%s AND user_id=%s AND model_id=%s
                """,
                (status, storage_path, model_file,
                 json.dumps(metadata_json, ensure_ascii=False),
                 json.dumps(metrics, ensure_ascii=False), now,
                 tenant_id, user_id, model_id),
            )
            action = "UPDATE"
        else:
            cur.execute(
                """
                INSERT INTO qm_user_models (
                    tenant_id, user_id, model_id, source_run_id, status, storage_path, model_file,
                    metadata_json, metrics_json, is_default, created_at, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, FALSE, %s, %s)
                """,
                (tenant_id, user_id, model_id, str(metadata.get("run_id") or ""),
                 status, storage_path, model_file,
                 json.dumps(metadata_json, ensure_ascii=False),
                 json.dumps(metrics, ensure_ascii=False),
                 now, now),
            )
            action = "INSERT"
    conn.commit()
    return f"{action} {model_id} -> {status} (file={model_file or 'NONE'})"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="批量注册模型目录到 qm_user_models")
    ap.add_argument("--models-root", default="models", help="models 根目录(默认 models/)")
    ap.add_argument("--tenant-id", default="default")
    ap.add_argument("--user-id", required=True, help="注册到哪个用户(users 表主键, 如 00000001)")
    ap.add_argument("--force", action="store_true", help="已存在也覆盖更新")
    ap.add_argument("--dry-run", action="store_true", help="只预览不写库")
    args = ap.parse_args(argv)

    root = Path(args.models_root)
    dirs = find_model_dirs(root)
    if not dirs:
        print(f"[!] {root}/users/ 下未找到含 metadata.json 的模型目录，先解压模型包：")
        print("    mkdir -p models && tar xzf models_share/smoke_13_models_20260825.tar.gz -C models")
        return 1

    conn = _db_connect() if not args.dry_run else None
    done = 0
    for d in dirs:
        if args.dry_run:
            metadata = json.loads((d / "metadata.json").read_text(encoding="utf-8"))
            model_id = metadata.get("model_id") or d.name
            print(f"[DRY ] {model_id} -> {quality_gate_status(metadata)} ({d})")
            done += 1
            continue
        try:
            print(f"  {register(conn, tenant_id=args.tenant_id, user_id=args.user_id, model_dir=d, force=args.force)}")
            done += 1
        except Exception as e:  # noqa: BLE001
            print(f"[ERR] {d.name}: {e}")
    if conn:
        conn.close()
    print(f"done={done}/{len(dirs)}")
    return 0 if done else 1


if __name__ == "__main__":
    sys.exit(main())
