"""sim テスト共通フィクスチャ。要求は core.protocol.private のエンコーダ、応答の検証は decode。"""

from __future__ import annotations

import pytest

from edulite05.core.device.profile import MotorProfile
from edulite05.core.protocol import private as pv
from edulite05.core.protocol.private import DecodeContext
from edulite05.core.protocol.types import DType, RunMode
from edulite05.core.transport.base import CanFrame
from edulite05.sim.motor_model import BENCH_MOTOR_ID, SimMotor

H = 0xFD
M = BENCH_MOTOR_ID  # 127


@pytest.fixture(scope="session")
def profile() -> MotorProfile:
    return MotorProfile.load_builtin("el05")


@pytest.fixture(scope="session")
def ctx(profile) -> DecodeContext:
    return DecodeContext(H, profile.ranges, profile.param_dtypes())


@pytest.fixture
def motor(profile) -> SimMotor:
    return SimMotor(motor_id=M, profile=profile)


class Bench:
    """SimMotor に要求を送って応答を decode する小道具。"""

    def __init__(self, motor: SimMotor, profile: MotorProfile, ctx: DecodeContext) -> None:
        self.m = motor
        self.p = profile
        self.ctx = ctx

    def ask(self, frame: CanFrame) -> list[CanFrame]:
        return self.m.handle(frame)

    def one(self, frame: CanFrame) -> CanFrame:
        out = self.m.handle(frame)
        assert len(out) == 1, out
        return out[0]

    def write(self, key: str, value) -> CanFrame:
        pd = self.p.param(key)
        return self.one(pv.write_param(self.m.motor_id, H, pd.index, pd.dtype, value))

    def read(self, key_or_index):
        pd = self.p.param(key_or_index)
        return pv.decode(self.one(pv.read_param(self.m.motor_id, H, pd.index)), self.ctx)

    def enable(self) -> CanFrame:
        return self.one(pv.enable(self.m.motor_id, H))

    def stop(self, clear: bool = False) -> CanFrame:
        return self.one(pv.stop(self.m.motor_id, H, clear))

    def set_mode(self, mode: RunMode) -> None:
        self.write("run_mode", int(mode))
        assert self.m.run_mode == int(mode)


@pytest.fixture
def bench(motor, profile, ctx) -> Bench:
    return Bench(motor, profile, ctx)


__all__ = ["DType", "H", "M", "Bench"]
