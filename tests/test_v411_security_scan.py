# -*- coding: utf-8 -*-
"""V4.1.1 P2: repository security scan EML fixture rules."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _load_scan_module():
    path = Path(__file__).resolve().parent.parent / "scripts" / "security_scan.py"
    spec = importlib.util.spec_from_file_location("paodan_security_scan", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_fixture_requires_header_or_manifest(tmp_path, monkeypatch):
    mod = _load_scan_module()
    monkeypatch.setattr(mod, "ROOT", tmp_path)
    manifest = tmp_path / "manifest.json"
    monkeypatch.setattr(mod, "MANIFEST_FILE", manifest)
    p = tmp_path / "tests" / "fixtures" / "x.eml"
    p.parent.mkdir(parents=True)
    p.write_text("X-Paodan-Synthetic-Fixture: true\nSubject: x\n\nbody", encoding="utf-8")
    ok, reason = mod.is_synthetic_eml(p, {})
    assert ok and reason == "header"

    p2 = tmp_path / "tests" / "fixtures" / "y.eml"
    p2.write_text("Subject: y\n\nbody", encoding="utf-8")
    ok2, _ = mod.is_synthetic_eml(p2, {})
    assert not ok2
    rel = p2.relative_to(tmp_path).as_posix()
    sha = hashlib.sha256(p2.read_bytes()).hexdigest()
    ok3, reason3 = mod.is_synthetic_eml(p2, {rel: sha})
    assert ok3 and reason3 == "manifest"
