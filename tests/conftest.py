"""Shared test setup: every test runs on a fresh temporary copy of data/, so real files are never touched."""
import json
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import planner_core as core  # noqa: E402


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    """Copy data/ to a temp folder, empty the schedule, and point the planner at it (Student A)."""
    d = tmp_path / "data"
    shutil.copytree(ROOT / "data", d)
    (d / "schedule.json").write_text(json.dumps({"semester": "Spring 2027", "courses": []}))
    monkeypatch.setattr(core, "DATA_DIR", d)
    monkeypatch.setattr(core, "STUDENT_FILE", "student.json")
    return d


@pytest.fixture
def student_b(monkeypatch):
    """Switch to Demo Student B (CMPS 202 = 65, no petitions)."""
    monkeypatch.setattr(core, "STUDENT_FILE", "student_low_grade.json")


def scheduled_codes():
    return [c["code"] for c in core.load_schedule()["courses"]]
