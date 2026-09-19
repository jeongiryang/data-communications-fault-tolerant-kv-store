#!/usr/bin/env bash

set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_dir"

echo "[1/4] 최신 main 코드를 받습니다."
git switch main
git pull --ff-only origin main

echo "[2/4] 실행 환경과 테스트를 확인합니다."
if [[ ! -x .venv/bin/python ]]; then
    python3 -m venv .venv
fi
.venv/bin/python -m pip install -e .
.venv/bin/python -m unittest discover -s tests -q

echo "[3/4] 기존 등록 확인용 서버를 비활성화합니다."
sudo systemctl disable --now kvstore-master.service 2>/dev/null || true

run_id="$(date +%Y%m%d-%H%M%S)"
log_dir="/home/ubuntu/kvstore-manual-test-logs/$run_id"
mkdir -p "$log_dir"

echo "[4/4] Master를 실행합니다."
echo "Master 로그: $log_dir/Master.txt"
echo "Worker 4개가 연결될 때까지 이 창을 닫지 마세요."

exec .venv/bin/python -m kvstore.master.runtime \
    --host 0.0.0.0 \
    --port 5000 \
    --log-dir "$log_dir"
