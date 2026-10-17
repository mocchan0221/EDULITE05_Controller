"""EL05 の STEP (Creo 出力) を GLB へ変換する(phase0-4)。

実行(pyproject には依存を追加しない。一時環境で実行する)::

    uv run --no-project --with cascadio --with trimesh --with numpy python tools/convert_step.py

プレビュー画像も出す場合は ``--with pyvista`` を追加して ``--preview`` を付ける。

処理の流れ
1. cascadio(OCCT)で STEP を GLB へ変換(部品ごとのノード構造を保持)。ねじ用に粗い許容差でも変換する。
2. Creo の ``\\X2\\..\\X0\\`` エンコード名(cascadio 出力では ``_X2_<UTF-16BE hex>_X0_``)を復号し、
   部品を英語 ID・rotor/stator に振り分ける。
3. 同心部品の外周円を最小二乗フィットして回転軸(Z 平行)の (x, y) を求め、原点へ平行移動する。
   Z は出力軸の出力側端面(CAD 上 z=39.75 mm)を 0 とする。単位は m。
4. ねじ 4 本は粗い許容差のメッシュに差し替える(三角形数を約 1/14 に削減)。
5. ``rotor`` / ``stator`` の 2 グループ(空ノード)の下に部品ノードを並べて GLB に書き出し、再読込して検証する。

結果・根拠の詳細は docs/research/el05_model.md。
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
import tempfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_STEP = ROOT / "docs" / "vendor" / "el05" / "el05.stp"
DEFAULT_OUT = ROOT / "assets" / "models" / "el05.glb"
DEFAULT_PREVIEW = ROOT / "assets" / "models" / "preview.png"

# 本体用(細かい)とねじ用(粗い)の変換許容差 (tol_linear [mm], tol_angular [rad])。
FINE_TOL = (0.01, 0.5)
COARSE_TOL = (1.0, 1.5)
MM = 1e-3

# 復号した中国語名 → (part_id, group)。同名が複数ある部品は幾何/ノード名で区別する(classify 参照)。
ZH_TABLE = {
    "输出轴": ("output_shaft", "rotor"),   # 出力軸(遊星キャリア兼用)
    "机壳": ("housing", "stator"),        # ハウジング
    "齿圈": ("ring_gear", "stator"),      # リングギア
    "后盖板": ("rear_cover", "stator"),   # リアカバー
    "卡簧": ("circlip", "stator"),        # サークリップ
    "垫片": ("washer", "rotor"),          # ワッシャ
    "轴销": ("pin", "rotor"),             # (遊星)ピン
}
# 部品 ID → 表示色 (STEP に色がないため仮。ビューア側で変えてよい)
COLORS = {
    "output_shaft": (205, 210, 218), "pin": (232, 190, 70), "washer": (185, 140, 60),
    "bearing_main": (140, 150, 165), "bearing_center": (140, 150, 165),
    "ring_gear": (110, 118, 130), "housing": (70, 76, 88), "rear_cover": (85, 92, 105),
    "circlip": (150, 150, 150), "pcb": (30, 110, 70), "screw": (170, 170, 175),
}
# 回転軸の算出に使う、同心・回転対称な部品
AXIS_REF = ("output_shaft", "ring_gear", "bearing_main", "bearing_center", "circlip", "housing", "rear_cover")


def decode_name(name: str) -> str:
    """cascadio のノード名 ``_X2_<hex>_X0_<n>`` を復号する(末尾の連番は OCC 由来なので残る)。"""
    return re.sub(r"_X2_([0-9A-Fa-f]+)_X0_", lambda m: bytes.fromhex(m.group(1)).decode("utf-16-be"), name)


def fit_circle(xy: np.ndarray) -> tuple[np.ndarray, float]:
    """点群 (N,2) への代数的最小二乗円フィット。(中心, 半径)。"""
    a = np.c_[2 * xy, np.ones(len(xy))]
    sol = np.linalg.lstsq(a, (xy**2).sum(1), rcond=None)[0]
    return sol[:2], float(np.sqrt(sol[2] + sol[:2] @ sol[:2]))


def outer_circle(verts: np.ndarray, tol: float = 0.02 * MM) -> tuple[np.ndarray, float, np.ndarray]:
    """最外周(bbox 中心からの最大半径 ± tol)に乗る頂点で円フィット。(中心, 半径, 使った頂点)。"""
    rough = (verts[:, :2].min(0) + verts[:, :2].max(0)) / 2
    r = np.hypot(*(verts[:, :2] - rough).T)
    sel = verts[r > r.max() - tol]
    c, rad = fit_circle(sel[:, :2])
    return c, rad, sel


def classify(node: str, verts: np.ndarray) -> tuple[str, str, str]:
    """ノード名と幾何から (part_id の基底, group, 中国語名) を返す。"""
    zh = re.sub(r"\d+$", "", decode_name(node)) if "_X2_" in node else node
    if zh in ZH_TABLE:
        return (*ZH_TABLE[zh], zh)
    if zh == "轴承":
        # 外径 37 mm の主軸受(出力軸フランジ外周)と、外径 10 mm の中心軸受(出力軸の中心穴)
        if np.ptp(verts[:, :2], axis=0).max() > 20 * MM:
            return "bearing_main", "stator", zh
        return "bearing_center", "rotor", zh
    if node.startswith("LUOSI"):
        return "screw", "stator", "螺丝(LUOSI)"
    if node.startswith("00_1-4459"):       # 軸销のうち、名前がエンコードされていない 2 本(Creo の別名)
        return "pin", "rotor", "轴销"
    if node.startswith("00_1-5178"):       # 垫片のうち同上
        return "washer", "rotor", "垫片"
    if node.startswith("PRT0001"):
        return "pcb", "stator", "PRT0001"
    raise ValueError(f"未分類のノード: {node!r}")


def world_meshes(glb: Path) -> dict:
    """GLB のジオメトリ付きノードを、ワールド変換適用済み Trimesh(ノード名 → Trimesh)で返す。"""
    import trimesh

    scene = trimesh.load(str(glb), force="scene", process=False)
    out = {}
    for node in scene.graph.nodes_geometry:
        transform, geom = scene.graph[node]
        m = scene.geometry[geom].copy()
        m.apply_transform(transform)
        out[node] = m
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="EL05 STEP → GLB (rotor/stator, 単位 m, 回転軸=Z)")
    ap.add_argument("--step", type=Path, default=DEFAULT_STEP)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--preview", nargs="?", const=str(DEFAULT_PREVIEW), default=None,
                    help="pyvista で off-screen 描画した PNG の出力先(要 --with pyvista)。失敗時はスキップ")
    ap.add_argument("--fine-screws", action="store_true", help="ねじを細かいまま残す(検証用)")
    args = ap.parse_args()

    import cascadio
    import trimesh
    from trimesh.visual.material import PBRMaterial

    step = args.step.resolve()
    print(f"入力: {step} ({step.stat().st_size} bytes, sha256={hashlib.sha256(step.read_bytes()).hexdigest()})")

    with tempfile.TemporaryDirectory() as td:
        fine_glb, coarse_glb = Path(td) / "fine.glb", Path(td) / "coarse.glb"
        for glb, tol in ((fine_glb, FINE_TOL), (coarse_glb, COARSE_TOL)):
            cascadio.step_to_glb(str(step), str(glb), tol_linear=tol[0], tol_angular=tol[1])
        fine, coarse = world_meshes(fine_glb), world_meshes(coarse_glb)

    tri_raw = sum(len(m.faces) for m in fine.values())
    print(f"変換直後(tol_linear={FINE_TOL[0]} mm, tol_angular={FINE_TOL[1]} rad): {len(fine)} ノード, 三角形 {tri_raw}")

    # --- 分類 + ねじの粗メッシュ差し替え ---
    parts = []   # (node, base, group, zh, mesh)
    for node, mesh in fine.items():
        base, group, zh = classify(node, mesh.vertices)
        if base == "screw" and not args.fine_screws:
            mesh = coarse[node]
        parts.append((node, base, group, zh, mesh))

    # --- 回転軸: 同心・回転対称部品の外周円フィット(CAD 座標) ---
    fits = {}
    for _node, base, _g, _zh, mesh in parts:
        if base in AXIS_REF:
            c, rad, _ = outer_circle(mesh.vertices)
            fits[base] = (c, rad)
    centers = np.array([c for c, _ in fits.values()])
    axis_xy = np.median(centers, axis=0)   # テッセレーション誤差の大きい部品に引っ張られにくい
    print("回転軸(CAD 座標, mm)— 部品別の外周円フィット:")
    for k, (c, r) in fits.items():
        print(f"  {k:15s} center=({c[0]/MM:9.4f}, {c[1]/MM:9.4f})  R={r/MM:7.3f}")
    print(f"  => 採用(中央値) ({axis_xy[0]/MM:.4f}, {axis_xy[1]/MM:.4f}) mm, 部品間ばらつき(ptp) {(np.ptp(centers, axis=0)/MM).round(4)} mm")

    # 傾き確認: ハウジング外周を z の下半分/上半分に分けて円中心を比較
    hv = next(m for _n, b, _g, _z, m in parts if b == "housing").vertices
    sel = outer_circle(hv)[2]
    zmid = (sel[:, 2].min() + sel[:, 2].max()) / 2
    lo, hi = sel[sel[:, 2] < zmid], sel[sel[:, 2] >= zmid]
    dc = np.hypot(*(fit_circle(hi[:, :2])[0] - fit_circle(lo[:, :2])[0]))
    dz = hi[:, 2].mean() - lo[:, 2].mean()
    print(f"  Z 平行性(ハウジング外周, Δz={dz/MM:.1f} mm): 中心差 {dc/MM*1000:.2f} um → 傾き {dc/dz*1000:.3f} mrad")

    z_face = next(m for _n, b, _g, _z, m in parts if b == "output_shaft").vertices[:, 2].min()
    shift = np.array([-axis_xy[0], -axis_xy[1], -z_face])
    print(f"平行移動 (m): {shift}  (Z=0 は出力軸の出力側端面 = CAD z {z_face/MM:.3f} mm)")

    # --- 名前付け(同種は軸まわりの角度順に連番)・材質・シーン構築 ---
    def angle(mesh) -> float:
        cx, cy = (mesh.bounds[0, :2] + mesh.bounds[1, :2]) / 2 - axis_xy
        return float(np.arctan2(cy, cx))

    counts: dict[str, int] = {}
    for _n, b, *_ in parts:
        counts[b] = counts.get(b, 0) + 1
    scene = trimesh.Scene()
    for g in ("stator", "rotor"):
        scene.graph.update(frame_from="world", frame_to=g, matrix=np.eye(4))
    seen: dict[str, int] = {}
    for node, base, group, zh, mesh in sorted(parts, key=lambda p: (p[1], angle(p[4]))):
        seen[base] = seen.get(base, 0) + 1
        pid = base if counts[base] == 1 else f"{base}_{seen[base]}"
        mesh = mesh.copy()
        mesh.apply_translation(shift)
        mesh.visual = trimesh.visual.TextureVisuals(material=PBRMaterial(
            name=pid, baseColorFactor=[*COLORS[base], 255], metallicFactor=0.6, roughnessFactor=0.45))
        mesh.metadata = {"step_node": node, "name_zh": zh, "group": group}
        scene.add_geometry(mesh, node_name=pid, geom_name=pid, parent_node_name=group)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    data = scene.export(file_type="glb")
    args.out.write_bytes(data)
    print(f"出力: {args.out} ({len(data)} bytes = {len(data)/1e6:.3f} MB)")

    verify(args.out, tri_raw)

    if args.preview:
        try:
            render_preview(args.out, Path(args.preview))
        except Exception as e:   # off-screen 描画が使えない環境ではスキップ
            print(f"プレビュー生成をスキップ: {type(e).__name__}: {e}")
    return 0


def verify(glb: Path, tri_raw: int) -> None:
    """書き出した GLB を再読込し、rotor/stator の 2 グループ・境界・三角形数・軸位置を確認する。"""
    import trimesh

    sc = trimesh.load(str(glb), force="scene", process=False)
    parents = sc.graph.transforms.parents
    groups = {g for g in ("rotor", "stator") if g in sc.graph.nodes}
    assert groups == {"rotor", "stator"}, f"グループノードが足りない: {groups}"
    tri = {"rotor": 0, "stator": 0}
    verts = []
    print("再読込での確認:")
    for node in sorted(sc.graph.nodes_geometry, key=lambda n: (parents[n], n)):
        grp = parents[node]
        assert grp in tri, f"{node} の親が rotor/stator でない: {grp}"
        t, g = sc.graph[node]
        v = sc.geometry[g].vertices @ t[:3, :3].T + t[:3, 3]
        verts.append(v)
        tri[grp] += len(sc.geometry[g].faces)
        print(f"  {grp:6s} {node:15s} tris={len(sc.geometry[g].faces):6d}  z=[{v[:,2].min()/MM:7.2f},{v[:,2].max()/MM:7.2f}] mm")
        if node in ("output_shaft", "ring_gear"):
            c, r, _ = outer_circle(v)
            print(f"         外周円フィット中心 ({c[0]/MM:.4f}, {c[1]/MM:.4f}) mm, R={r/MM:.3f} mm  ← 0,0 なら軸が原点")
    allv = np.vstack(verts)
    print(f"  rotor tris={tri['rotor']}, stator tris={tri['stator']}, 合計 {sum(tri.values())} (変換直後 {tri_raw})")
    print(f"  bounds (mm): min={(allv.min(0)/MM).round(3)}, max={(allv.max(0)/MM).round(3)}")
    assert tri["rotor"] > 0 and tri["stator"] > 0
    assert np.ptp(allv, axis=0).max() < 0.2, "単位が m でない可能性"


def render_preview(glb: Path, png: Path) -> None:
    """pyvista(off_screen)で rotor=橙 / stator=灰 を 2 視点(出力側・背面側)並べて描画する。"""
    import pyvista as pv
    import trimesh

    sc = trimesh.load(str(glb), force="scene", process=False)
    colors = {"rotor": "#e8a030", "stator": "#9aa3b0"}
    polys = []
    for node in sc.graph.nodes_geometry:
        t, g = sc.graph[node]
        m = sc.geometry[g].copy()
        m.apply_transform(t)
        polys.append((sc.graph.transforms.parents[node], pv.wrap(m)))

    pl = pv.Plotter(off_screen=True, shape=(1, 2), window_size=(1600, 800))
    views = (("output side (view from -Z)", (-0.07, -0.09, -0.11)), ("rear side (view from +Z)", (0.07, 0.09, 0.16)))
    for col, (title, pos) in enumerate(views):
        pl.subplot(0, col)
        for grp, pd in polys:
            pl.add_mesh(pd, color=colors[grp], smooth_shading=True)
        pl.add_axes()
        pl.add_text(title, font_size=10)
        pl.camera_position = [pos, (0, 0, 0.022), (0, 1, 0)]
    pl.screenshot(str(png))
    pl.close()
    print(f"プレビュー: {png}")


if __name__ == "__main__":
    sys.exit(main())
