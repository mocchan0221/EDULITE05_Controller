"""EL05 の 3D モデル(assets/models/el05.glb)の読込。

pyvista の GLTF リーダは glTF のノード名を保持しない(Node_0, Node_1 …)ため、GLB の JSON チャンクを
直接読んでノード階層(ルート `rotor` / `stator` とその子の部品名)を取得し、リーダが返す
MultiBlock の順序と対応付ける。trimesh には依存しない。

モデルの座標系(el05_model.md §5): 単位 m、回転軸 = 原点を通る Z、出力面 z = 0(-Z 向き、ピンが
z = -3 mm まで突出)、背面 z = +44 mm。`rotor` を原点まわりに Z 軸で回すだけでよい。
pyvista は重いので、このモジュールは import 時に pyvista を読まない(呼び出し側が渡す)。
"""

from __future__ import annotations

import json
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# 正の出力角に対する rotor の回転方向(+Z まわり右手系なら +1)。実機で確認済み:
# 正方向 = 出力面から見て時計回り(hw_bench.md HW-3)。
ROTOR_SIGN = 1.0

# 部品名(GLB ノード名)→ 仮の色。モデルの色は STEP に無いのでビューア側で決める
PART_COLORS: dict[str, str] = {
    "output_shaft": "#e08a2e",  # 出力円盤(プレビューと同じオレンジ)
    "pin": "#c4c9d1",
    "washer": "#a2a8b2",
    "bearing_center": "#8e949e",
    "bearing_main": "#9aa0aa",
    "ring_gear": "#7c838f",
    "housing": "#4b515c",
    "rear_cover": "#444a54",
    "pcb": "#2f8f5b",
    "screw": "#b4bac3",
    "circlip": "#c9ced6",
}

DEFAULT_COLOR = "#8a909a"


def glb_path() -> Path:
    """プロジェクトルートの assets/models/el05.glb。"""
    return Path(__file__).resolve().parents[3] / "assets" / "models" / "el05.glb"


def part_color(name: str) -> str:
    base = name.rstrip("0123456789").rstrip("_")
    return PART_COLORS.get(name) or PART_COLORS.get(base) or DEFAULT_COLOR


def read_glb_json(path: Path) -> dict[str, Any]:
    """GLB ファイルの JSON チャンクを返す。"""
    with path.open("rb") as f:
        magic, _version, _length = struct.unpack("<4sII", f.read(12))
        if magic != b"glTF":
            raise ValueError("not a GLB file")
        chunk_len, chunk_type = struct.unpack("<II", f.read(8))
        if chunk_type != 0x4E4F534A:  # "JSON"
            raise ValueError("first chunk is not JSON")
        return json.loads(f.read(chunk_len).decode("utf-8"))


def read_structure(path: Path) -> list[tuple[str, list[str]]]:
    """シーンのルートノード順に (ノード名, 子ノード名のリスト)。"""
    gltf = read_glb_json(path)
    nodes = gltf["nodes"]
    scene = gltf["scenes"][gltf.get("scene", 0)]
    out: list[tuple[str, list[str]]] = []
    for idx in scene["nodes"]:
        node = nodes[idx]
        kids = [nodes[c].get("name", f"node{c}") for c in node.get("children", [])]
        out.append((node.get("name", f"node{idx}"), kids))
    return out


@dataclass(slots=True)
class Part:
    name: str
    mesh: Any  # pyvista.PolyData
    color: str


@dataclass(slots=True)
class Model:
    rotor: list[Part]
    stator: list[Part]
    source: str  # "glb" / "cylinders"


def _leaf_mesh(pv: Any, block: Any) -> Any:
    """MultiBlock の入れ子から PolyData を 1 つに取り出す。"""
    if isinstance(block, pv.MultiBlock):
        leaves = [_leaf_mesh(pv, block[i]) for i in range(block.n_blocks)]
        leaves = [m for m in leaves if m is not None]
        if not leaves:
            return None
        return leaves[0] if len(leaves) == 1 else pv.merge(leaves)
    return block


def load_model(pv: Any, path: Path | None = None) -> Model:
    """GLB を読み、rotor / stator の部品リストにする。失敗時は円柱 2 つで代用する。"""
    path = path or glb_path()
    try:
        structure = read_structure(path)
        mb = pv.read(str(path))
        if mb.n_blocks != len(structure):
            raise ValueError("ノード数が一致しません")
        groups: dict[str, list[Part]] = {}
        for ri, (root_name, kids) in enumerate(structure):
            block = mb[ri]
            if not isinstance(block, pv.MultiBlock) or block.n_blocks != len(kids):
                raise ValueError(f"{root_name}: 子ノード数が一致しません")
            parts = []
            for ci, kid in enumerate(kids):
                mesh = _leaf_mesh(pv, block[ci])
                if mesh is not None and mesh.n_points > 0:
                    parts.append(Part(kid, mesh, part_color(kid)))
            groups[root_name] = parts
        if "rotor" not in groups or "stator" not in groups:
            raise ValueError("rotor / stator ノードがありません")
        return Model(groups["rotor"], groups["stator"], "glb")
    except Exception:
        return fallback_cylinders(pv)


def fallback_cylinders(pv: Any) -> Model:
    """GLB が無い/読めない場合の代用(ハウジング円柱 + 出力円盤)。単位 m、軸 = Z。"""
    housing = pv.Cylinder(center=(0, 0, 0.022), direction=(0, 0, 1), radius=0.023,
                          height=0.044, resolution=64)
    disc = pv.Cylinder(center=(0, 0, 0.005), direction=(0, 0, 1), radius=0.016, height=0.01,
                       resolution=64)
    pin = pv.Cylinder(center=(0.0088, 0, 0.004), direction=(0, 0, 1), radius=0.002,
                      height=0.014, resolution=16)
    return Model(
        rotor=[Part("output_shaft", disc, PART_COLORS["output_shaft"]),
               Part("pin", pin, PART_COLORS["pin"])],
        stator=[Part("housing", housing, PART_COLORS["housing"])],
        source="cylinders",
    )
