"""コア層の例外(phase1.md §0)。"""

from __future__ import annotations


class EduliteError(Exception):
    """edulite05 コアの例外の基底。"""


class TransportError(EduliteError):
    """トランスポート(CAN アダプタ)層のエラー。"""


class ProtocolError(EduliteError):
    """プロトコル層のエラー(不正な値・状態など)。"""


class RequestTimeout(EduliteError, TimeoutError):
    """要求に対する応答が期限内に来なかった。"""


class ProfileError(EduliteError):
    """モータプロファイル(TOML)の読込・検証の失敗。"""


class SafetyError(EduliteError):
    """安全ガードが指令を拒否した(NaN / inf・不正な種別・不正な上限値など)。"""
