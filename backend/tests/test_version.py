"""backend/shared/version.py 单元测试。"""
from __future__ import annotations

from pathlib import Path

import backend.shared.version as vmod


def test_version_falls_back_to_dev(tmp_path: Path, monkeypatch):
    """无 version.txt → 回退 dev。"""
    monkeypatch.setattr(vmod, "_VERSION_TXT", tmp_path / "nonexistent.txt")
    assert vmod.get_version() == "dev"


def test_version_reads_file(tmp_path: Path, monkeypatch):
    vtxt = tmp_path / "version.txt"
    vtxt.write_text("v1.10.0\n", encoding="utf-8")
    monkeypatch.setattr(vmod, "_VERSION_TXT", vtxt)
    assert vmod.get_version() == "v1.10.0"


def test_version_ignores_blank(tmp_path: Path, monkeypatch):
    vtxt = tmp_path / "version.txt"
    vtxt.write_text("   \n", encoding="utf-8")
    monkeypatch.setattr(vmod, "_VERSION_TXT", vtxt)
    assert vmod.get_version() == "dev"


def test_version_strips_whitespace(tmp_path: Path, monkeypatch):
    vtxt = tmp_path / "version.txt"
    vtxt.write_text("v1.9.0-beta-150-g3d32379f\n", encoding="utf-8")
    monkeypatch.setattr(vmod, "_VERSION_TXT", vtxt)
    assert vmod.get_version() == "v1.9.0-beta-150-g3d32379f"
