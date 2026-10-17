"""モック用の 0x70xx パラメータ定義表(el05_protocol.md §5.2)。

CLAUDE.md の原則上、本来はプロファイル TOML(`profiles/el05.toml`)に置くデータ。
Phase 1 でプロファイル読込ができるまでの暫定として、モック(DemoBridge)だけが使う。
"""

from __future__ import annotations

from edulite05.app.viewmodels import ParamDef

_D = ParamDef

EL05_PARAM_DEFS: tuple[ParamDef, ...] = (
    _D(0x7005, "run_mode", "uint8", "", "0,1,2,3,5", "W/R",
       "0 運控 / 1 PP / 2 速度 / 3 電流 / 5 CSP", 0, 0, 5),
    _D(0x7006, "iq_ref", "float", "A", "-11 ~ 11", "W/R", "電流モード Iq 指令", 0.0, -11.0, 11.0),
    _D(0x700A, "spd_ref", "float", "rad/s", "-50 ~ 50", "W/R", "速度モード 速度指令",
       0.0, -50.0, 50.0),
    _D(0x700B, "limit_torque", "float", "N·m", "0 ~ 6", "W/R", "トルク制限", 6.0, 0.0, 6.0),
    _D(0x7010, "cur_kp", "float", "", "", "W/R", "電流ループ Kp", 0.17),
    _D(0x7011, "cur_ki", "float", "", "", "W/R", "電流ループ Ki", 0.012),
    _D(0x7014, "cur_filt_gain", "float", "", "0 ~ 1.0", "W/R", "電流フィルタ係数", 0.1, 0.0, 1.0),
    _D(0x7016, "loc_ref", "float", "rad", "", "W/R", "位置モード角度指令(PP/CSP)", 0.0),
    _D(0x7017, "limit_spd", "float", "rad/s", "0 ~ 50", "W/R", "位置モード(CSP)速度制限",
       2.0, 0.0, 50.0),
    _D(0x7018, "limit_cur", "float", "A", "0 ~ 11", "W/R", "速度・位置モード 電流制限",
       11.0, 0.0, 11.0),
    _D(0x7019, "mechPos", "float", "rad", "", "R", "負荷端 多回転機械角", 0.0),
    _D(0x701A, "iqf", "float", "A", "-11 ~ 11", "R", "iq フィルタ値", 0.0),
    _D(0x701B, "mechVel", "float", "rad/s", "-50 ~ 50", "R", "負荷端 回転速度", 0.0),
    _D(0x701C, "VBUS", "float", "V", "", "R", "母線電圧", 24.0),
    _D(0x701E, "loc_kp", "float", "", "", "W/R", "位置ループ Kp", 40.0),
    _D(0x701F, "spd_kp", "float", "", "", "W/R", "速度ループ Kp", 6.0),
    _D(0x7020, "spd_ki", "float", "", "", "W/R", "速度ループ Ki", 0.02),
    _D(0x7021, "spd_filt_gain", "float", "", "", "W/R", "速度フィルタ値", 0.1),
    _D(0x7022, "acc_rad", "float", "rad/s²", "", "W/R", "速度モード加速度", 20.0),
    _D(0x7024, "vel_max", "float", "rad/s", "", "W/R", "PP モード速度", 10.0),
    _D(0x7025, "acc_set", "float", "rad/s²", "", "W/R", "PP モード加速度", 10.0),
    _D(0x7026, "EPScan_time", "uint16", "", "0 = 10 ms, +1 ごとに +5 ms", "W/R",
       "主動上報の周期", 0, 0, 65535),
    _D(0x7028, "canTimeout", "uint32", "", "20000 = 1 s, 0 = 無効", "W/R",
       "CAN タイムアウト閾値(50 µs/count)", 0, 0),
    _D(0x7029, "zero_sta", "uint8", "", "0, 1", "W/R",
       "ゼロ点フラグ。0 → 0~2π、1 → -π~π", 0, 0, 1),
    _D(0x702B, "add_offset", "float", "", "", "W/R", "ゼロ位オフセット", 0.0),
    _D(0x702C, "alveolous_open", "uint8", "", "0, 1", "W/R", "コギング補償スイッチ", 0, 0, 1),
    _D(0x702D, "iq_test", "uint8", "", "0, 1", "W/R", "モータ初期化校正スイッチ", 0, 0, 1),
    _D(0x702E, "dcc_set", "float", "rad/s²", "", "W/R", "PP モード減速度(通常は無効)", 10.0),
)
