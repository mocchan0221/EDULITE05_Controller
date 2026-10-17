"""`MotorViewState` の時系列を CSV に書く(DemoBridge の記録と、ブリッジなしの `Recorder` が使う)。

Qt を使わない。列は `t_s` と、モータごとの `m<ID>_<接尾辞>`(位置・速度・トルク・温度など)。
"""

from __future__ import annotations

import csv
from collections.abc import Sequence
from pathlib import Path

from edulite05.app.viewmodels import MotorViewState

# 列の定義: (接尾辞, MotorViewState の属性, 小数桁)
COLUMNS: tuple[tuple[str, str, int], ...] = (
    ("pos_rad", "position_multi", 5),
    ("vel_rad_s", "velocity", 4),
    ("torque_Nm", "torque", 4),
    ("temp_C", "temperature", 2),
    ("vbus_V", "vbus", 3),
    ("cmd_pos_rad", "cmd_position", 5),
    ("cmd_vel_rad_s", "cmd_velocity", 4),
    ("cmd_torque_Nm", "cmd_torque", 4),
)


def write_state_csv(path: Path, rows: Sequence[tuple[float, dict[int, MotorViewState]]]) -> None:
    """`rows` = (記録開始からの秒, {モータ ID: 状態}) の列を `path` に書く。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    ids = sorted({mid for _t, st in rows for mid in st})
    header = ["t_s"] + [f"m{mid}_{suffix}" for mid in ids for suffix, _a, _d in COLUMNS]
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        for t, st in rows:
            line: list[str] = [f"{t:.4f}"]
            for mid in ids:
                s = st.get(mid)
                for _suffix, attr, dec in COLUMNS:
                    v = getattr(s, attr, None) if s is not None else None
                    line.append("" if v is None else f"{v:.{dec}f}")
            w.writerow(line)
