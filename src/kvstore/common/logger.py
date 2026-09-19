"""Master와 Worker가 함께 사용하는 파일 로거.

과제 요구사항:
- 형식: [clock] NODE | EVENT | STATUS | message
- STATUS는 INFO, SUCCESS, FAIL, WARN만 허용한다.
- 각 노드별로 독립된 파일(Master.txt, Worker1.txt~Worker4.txt)에 기록한다.
- 멀티스레드 환경에서 로그가 섞이지 않도록 Lock으로 동기화한다.
"""
from __future__ import annotations

import threading
from pathlib import Path

# 과제 명세에 정의된 4가지 허용 STATUS
VALID_STATUSES = frozenset({"INFO", "SUCCESS", "FAIL", "WARN"})


def format_log_line(clock: float, node: str, event: str, status: str, message: str) -> str:
    """과제 규격에 맞게 로그 문자열을 생성한다."""
    upper_status = status.upper()
    if upper_status not in VALID_STATUSES:
        raise ValueError(
            f"유효하지 않은 STATUS: '{status}'. "
            f"과제 규격상 INFO, SUCCESS, FAIL, WARN만 사용할 수 있습니다."
        )
    # 논리 시계는 소수점 둘째 자리까지 표기해 가독성을 높인다.
    return f"[{clock:.2f}] {node} | {event} | {upper_status} | {message}"


class NodeLogger:
    """노드(Master 또는 Worker)별 로그 파일 쓰기와 출력을 담당하는 클래스."""

    def __init__(
        self,
        node_name: str,
        log_file_path: str | Path | None = None,
        print_to_console: bool = True,
    ) -> None:
        self.node_name = node_name
        self.log_file_path = Path(log_file_path) if log_file_path else None
        self.print_to_console = print_to_console
        # 여러 스레드가 동시에 파일에 쓸 때 내용이 깨지지 않도록 Lock을 둔다.
        self._lock = threading.Lock()
        self._file_handle = None

        if self.log_file_path is not None:
            # 상위 디렉터리가 없으면 생성한다.
            self.log_file_path.parent.mkdir(parents=True, exist_ok=True)
            # UTF-8로 열어 한글 메시지가 깨지지 않게 한다.
            self._file_handle = open(self.log_file_path, "a", encoding="utf-8")

    def log(self, clock: float, event: str, status: str, message: str) -> str:
        """이벤트를 규격에 맞춰 포맷팅하고 파일 및 콘솔에 기록한다."""
        line = format_log_line(clock, self.node_name, event, status, message)
        with self._lock:
            if self.print_to_console:
                print(line, flush=True)
            if self._file_handle is not None and not self._file_handle.closed:
                self._file_handle.write(line + "\n")
                self._file_handle.flush()
        return line

    def __call__(self, clock: float, node: str, event: str, status: str, message: str) -> None:
        """Runtime에서 사용하는 로그 함수 형태를 지원한다."""
        target_node = node or self.node_name
        line = format_log_line(clock, target_node, event, status, message)
        with self._lock:
            if self.print_to_console:
                print(line, flush=True)
            if self._file_handle is not None and not self._file_handle.closed:
                self._file_handle.write(line + "\n")
                self._file_handle.flush()

    def close(self) -> None:
        """열려 있는 로그 파일 핸들을 안전하게 닫는다."""
        with self._lock:
            if self._file_handle is not None and not self._file_handle.closed:
                self._file_handle.close()
                self._file_handle = None

    def __enter__(self) -> NodeLogger:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()
