from __future__ import annotations

import sys
import traceback
from pathlib import Path

import numpy as np

from photo_report_app.pointcloud_advanced_viewer import run_advanced_viewer


def _optional_rgb(values: np.ndarray) -> np.ndarray | None:
    return values if values.ndim == 2 and len(values) else None


def main() -> int:
    if len(sys.argv) != 2:
        return 2
    source = Path(sys.argv[1])
    with np.load(source, allow_pickle=False) as data:
        payload = {
            "scanner_xyz": data["scanner_xyz"].copy(),
            "scanner_rgb": _optional_rgb(data["scanner_rgb"].copy()),
            "drone_raw_xyz": data["drone_raw_xyz"].copy(),
            "drone_adjusted_xyz": data["drone_adjusted_xyz"].copy(),
            "drone_rgb": _optional_rgb(data["drone_rgb"].copy()),
            "names": {
                "scanner": str(data["scanner_name"].item()),
                "drone": str(data["drone_name"].item()),
            },
        }
    run_advanced_viewer(payload)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:
        if len(sys.argv) == 2:
            source = Path(sys.argv[1])
            source.with_suffix(".error.txt").write_text(traceback.format_exc(), encoding="utf-8")
        raise
