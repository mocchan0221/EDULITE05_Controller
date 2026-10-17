"""シーケンス(YAML の手順を MotorService に対して実行する)。Qt を import しない。

    from edulite05.core.sequence import load_sequence, SequenceRunner

    seq = load_sequence("sequences/velocity_steps.yaml")     # 実行前にすべて検証(行番号つき)
    runner = SequenceRunner(service, seq, motor_id=127)
    runner.start()                                           # ワーカースレッド
    runner.abort()                                           # いつでも中断(必ず停止・無効化)

指令はすべて `MotorService`(SafetyGuard)経由。出力段階は LOW のみ。
"""

from edulite05.core.sequence.runner import (
    Progress,
    SequenceEvent,
    SequenceResult,
    SequenceRunner,
    SequenceState,
)
from edulite05.core.sequence.schema import (
    Problem,
    Sequence,
    SequenceError,
    Step,
    list_sequences,
    load_sequence,
    parse_sequence,
)

__all__ = [
    "Problem",
    "Progress",
    "Sequence",
    "SequenceError",
    "SequenceEvent",
    "SequenceResult",
    "SequenceRunner",
    "SequenceState",
    "Step",
    "list_sequences",
    "load_sequence",
    "parse_sequence",
]
