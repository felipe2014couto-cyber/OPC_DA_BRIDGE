"""Host-only regression test for the platform-neutral native Mirror state model."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
MIRROR = ROOT / "windows" / "softing_mirror_server"


@pytest.mark.parametrize("compiler", ["g++", "clang++"])
def test_snapshot_store_native_contract(tmp_path: Path, compiler: str) -> None:
    executable = shutil.which(compiler)
    if executable is None:
        pytest.skip(f"{compiler} is not installed")
    output = tmp_path / "snapshot_store_test"
    subprocess.run(
        [
            executable,
            "-std=c++11",
            "-Wall",
            "-Wextra",
            "-Werror",
            str(MIRROR / "src" / "SnapshotStore.cpp"),
            str(MIRROR / "tests" / "snapshot_store_test.cpp"),
            "-o",
            str(output),
        ],
        check=True,
        cwd=ROOT,
    )
    subprocess.run([str(output)], check=True, cwd=ROOT)
