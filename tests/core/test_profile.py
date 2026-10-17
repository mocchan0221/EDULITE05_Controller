"""MotorProfile と profiles/el05.toml(el05_protocol.md §1.5 / §5.2、phase1.md §1.5)。"""

import pytest

from edulite05.core.device.profile import GROUPS, MotorProfile, find_profile_path
from edulite05.core.errors import ProfileError
from edulite05.core.protocol.types import DType, Ranges

# el05_protocol.md §5.2 の全行(index, key, dtype, access)
SPEC_ROWS = [
    (0x7005, "run_mode", "uint8", "rw"), (0x7006, "iq_ref", "float", "rw"),
    (0x700A, "spd_ref", "float", "rw"), (0x700B, "limit_torque", "float", "rw"),
    (0x7010, "cur_kp", "float", "rw"), (0x7011, "cur_ki", "float", "rw"),
    (0x7014, "cur_filt_gain", "float", "rw"), (0x7016, "loc_ref", "float", "rw"),
    (0x7017, "limit_spd", "float", "rw"), (0x7018, "limit_cur", "float", "rw"),
    (0x7019, "mechPos", "float", "r"), (0x701A, "iqf", "float", "r"),
    (0x701B, "mechVel", "float", "r"), (0x701C, "VBUS", "float", "r"),
    (0x701E, "loc_kp", "float", "rw"), (0x701F, "spd_kp", "float", "rw"),
    (0x7020, "spd_ki", "float", "rw"), (0x7021, "spd_filt_gain", "float", "rw"),
    (0x7022, "acc_rad", "float", "rw"), (0x7024, "vel_max", "float", "rw"),
    (0x7025, "acc_set", "float", "rw"), (0x7026, "EPScan_time", "uint16", "rw"),
    (0x7028, "canTimeout", "uint32", "rw"), (0x7029, "zero_sta", "uint8", "rw"),
    (0x702B, "add_offset", "float", "rw"), (0x702C, "alveolous_open", "uint8", "rw"),
    (0x702D, "iq_test", "uint8", "rw"), (0x702E, "dcc_set", "float", "rw"),
]


@pytest.fixture(scope="module")
def prof():
    return MotorProfile.load_builtin("el05")


def test_motor_info(prof):
    assert prof.model == "EL05"
    assert prof.name == "RobStride EduLite 05"


def test_ranges(prof):
    assert prof.ranges == Ranges(
        position=(-12.57, 12.57),
        velocity=(-50.0, 50.0),
        torque=(-6.0, 6.0),
        kp=(0.0, 500.0),
        kd=(0.0, 5.0),
    )


def test_limits(prof):
    assert (prof.limits.current, prof.limits.speed) == (11.0, 50.0)
    assert (prof.limits.torque, prof.limits.temp_fault) == (6.0, 135.0)


def test_power_tiers(prof):
    low, high = prof.tier("low"), prof.tier("high")
    assert (low.torque, low.current, low.speed, low.kp, low.kd) == (0.5, 1.0, 5.0, 20.0, 1.0)
    got = (high.torque, high.current, high.speed, high.kp, high.kd)
    assert got == (6.0, 11.0, 50.0, 500.0, 5.0)
    with pytest.raises(KeyError):
        prof.tier("mid")


def test_all_spec_rows_present_in_order(prof):
    assert [(p.index, p.key, p.dtype.value, p.access) for p in prof.params] == SPEC_ROWS


def test_param_lookup_by_index_and_key(prof):
    p = prof.param(0x7005)
    assert p is prof.param("run_mode")
    got = (p.key, p.dtype, p.unit, p.min, p.max, p.default)
    assert got == ("run_mode", DType.UINT8, "", 0, 5, 0)
    assert p.label == "動作モード(run_mode)" and p.group == "指令" and p.writable
    assert isinstance(p.default, int)
    with pytest.raises(KeyError):
        prof.param(0x7000)
    with pytest.raises(KeyError):
        prof.param("nope")


def test_param_value_types_follow_dtype(prof):
    for p in prof.params:
        want = float if p.dtype is DType.FLOAT else int
        assert isinstance(p.default, want), p.key
        for v in (p.min, p.max):
            assert v is None or isinstance(v, want), p.key


def test_param_dtypes(prof):
    dt = prof.param_dtypes()
    assert len(dt) == len(SPEC_ROWS)
    assert dt[0x7005] is DType.UINT8 and dt[0x7026] is DType.UINT16
    assert dt[0x7028] is DType.UINT32 and dt[0x701E] is DType.FLOAT


def test_groups_and_access(prof):
    assert {p.group for p in prof.params} <= set(GROUPS)
    # 読み取り専用は全て「状態」(app/pages/params/groups.py と同じ規則)
    assert {p.key for p in prof.params if not p.writable} == {"mechPos", "iqf", "mechVel", "VBUS"}
    assert all(p.group == "状態" for p in prof.params if not p.writable)
    assert prof.param("loc_kp").group == "制御ゲイン"
    assert prof.param("limit_cur").group == "制限"
    assert prof.param("zero_sta").group == "設定"
    assert prof.param("loc_ref").group == "指令"


def test_known_ranges_and_defaults(prof):
    assert (prof.param("iq_ref").min, prof.param("iq_ref").max) == (-11.0, 11.0)
    assert (prof.param("limit_cur").min, prof.param("limit_cur").max) == (0.0, 11.0)
    assert prof.param("cur_kp").default == pytest.approx(0.17)
    assert prof.param("acc_rad").default == 20.0 and prof.param("vel_max").default == 10.0
    assert prof.param("EPScan_time").default == 0 and prof.param("canTimeout").default == 0
    assert prof.param("spd_ref").unit == "rad/s"
    assert all(p.description and p.label for p in prof.params)


def test_load_by_path_and_find(prof):
    path = find_profile_path("el05")
    assert path.name == "el05.toml"
    assert MotorProfile.load(path).params == prof.params


def test_find_profile_missing():
    with pytest.raises(ProfileError):
        find_profile_path("nonexistent_motor")


def test_env_override(tmp_path, monkeypatch):
    src = find_profile_path("el05").read_text(encoding="utf-8")
    (tmp_path / "custom.toml").write_text(src, encoding="utf-8")
    monkeypatch.setenv("EDULITE05_PROFILES_DIR", str(tmp_path))
    assert find_profile_path("custom").parent == tmp_path
    assert MotorProfile.load_builtin("custom").model == "EL05"


def test_load_invalid(tmp_path):
    bad = tmp_path / "bad.toml"
    bad.write_text("[motor]\nmodel = 'x'\n", encoding="utf-8")
    with pytest.raises(ProfileError):
        MotorProfile.load(bad)
    with pytest.raises(ProfileError):
        MotorProfile.load(tmp_path / "missing.toml")
    broken = tmp_path / "broken.toml"
    broken.write_text("[motor\n", encoding="utf-8")
    with pytest.raises(ProfileError):
        MotorProfile.load(broken)


def test_duplicate_index_rejected(tmp_path):
    text = find_profile_path("el05").read_text(encoding="utf-8")
    dup = text + (
        '\n[[params]]\nindex = 0x7005\nkey = "dup"\nlabel = "x"\ngroup = "設定"\n'
        'dtype = "uint8"\ndefault = 0\naccess = "rw"\n'
    )
    f = tmp_path / "dup.toml"
    f.write_text(dup, encoding="utf-8")
    with pytest.raises(ProfileError):
        MotorProfile.load(f)
