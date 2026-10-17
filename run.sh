#!/usr/bin/env sh
# EDULITE05 Controller 起動スクリプト(Linux 用)
cd "$(dirname "$0")" || exit 1
if ! command -v uv >/dev/null 2>&1; then
    echo "[ERROR] uv が見つかりません。インストール: curl -LsSf https://astral.sh/uv/install.sh | sh" >&2
    exit 1
fi
# venv はローカルディスクに置く(共有フォルダ上だと遅いため)
export UV_PROJECT_ENVIRONMENT="${UV_PROJECT_ENVIRONMENT:-$HOME/.local/share/edulite05/venv}"
exec uv run --frozen python -m edulite05 "$@"
