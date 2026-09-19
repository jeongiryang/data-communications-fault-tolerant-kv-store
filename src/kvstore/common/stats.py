"""과제의 필수 성능 지표를 Thread-safe하게 수집한다.

과제 필수 지표:
1. Worker별 작업 처리량 (성공 처리 건수)
2. Worker별 성공 / 실패 횟수 (80% 성공, 20% 실패 횟수)
3. 작업 평균 대기시간 (초, 큐 인입부터 처리 시작까지)
4. P2P 부하 분산 이벤트 횟수 (Worker 간 이전 발생 횟수)
5. 장애 재할당 횟수 (Priority Queue를 거쳐 다른 Worker로 재배치된 횟수)
6. 전체 수행시간 (초, System Clock 기준)
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any


@dataclass
class WorkerStats:
    """단일 Worker의 처리 통계."""

    worker_id: str
    throughput: int = 0  # 성공 처리된 작업 수
    success_count: int = 0  # 성공 횟수
    fail_count: int = 0  # 실패 횟수 (20% 규칙)
    overflow_count: int = 0  # 큐 가득 참으로 인한 거절 횟수
    total_wait_seconds: float = 0.0  # 큐 대기시간 누적합
    processed_count: int = 0  # 대기시간 계산용 총 처리 건수
    p2p_transfers_sent: int = 0  # P2P 송신 시도 횟수
    p2p_tasks_sent: int = 0  # P2P로 넘겨준 작업 개수
    p2p_transfers_received: int = 0  # P2P 수신 완료 횟수
    p2p_tasks_received: int = 0  # P2P로 전달받은 작업 개수

    @property
    def average_wait_seconds(self) -> float:
        """작업 1건당 평균 큐 대기시간."""
        if self.processed_count == 0:
            return 0.0
        return self.total_wait_seconds / self.processed_count

    def to_dict(self) -> dict[str, Any]:
        return {
            "worker_id": self.worker_id,
            "throughput": self.throughput,
            "success_count": self.success_count,
            "fail_count": self.fail_count,
            "overflow_count": self.overflow_count,
            "average_wait_seconds": round(self.average_wait_seconds, 3),
            "p2p_transfers_sent": self.p2p_transfers_sent,
            "p2p_tasks_sent": self.p2p_tasks_sent,
            "p2p_transfers_received": self.p2p_transfers_received,
            "p2p_tasks_received": self.p2p_tasks_received,
        }


class StatsCollector:
    """전체 시뮬레이션의 통계 데이터를 Thread-safe하게 수집하고 계산하는 집계기."""

    def __init__(self) -> None:
        # 동일 스레드 내부 재진입 및 멀티스레드 동시성 안전을 위해 RLock 사용
        self._lock = threading.RLock()
        self._workers: dict[str, WorkerStats] = {}
        self._total_reallocations: int = 0  # 실패로 인한 재할당 횟수
        self._total_p2p_events: int = 0  # 전체 P2P 이전 성공 횟수
        self._total_p2p_tasks: int = 0  # P2P로 이전된 총 작업 개수
        self._seen_event_ids: set[str] = set()

    def _is_duplicate_locked(self, event_id: str | None) -> bool:
        if event_id is None:
            return False
        if event_id in self._seen_event_ids:
            return True
        self._seen_event_ids.add(event_id)
        return False

    def _get_or_create_worker_locked(self, worker_id: str) -> WorkerStats:
        if worker_id not in self._workers:
            self._workers[worker_id] = WorkerStats(worker_id=worker_id)
        return self._workers[worker_id]

    def record_task_success(
        self, worker_id: str, wait_seconds: float, event_id: str | None = None
    ) -> bool:
        """작업이 80% 성공 처리되었을 때 기록한다."""
        with self._lock:
            if self._is_duplicate_locked(event_id):
                return False
            stats = self._get_or_create_worker_locked(worker_id)
            stats.throughput += 1
            stats.success_count += 1
            stats.processed_count += 1
            if wait_seconds > 0:
                stats.total_wait_seconds += float(wait_seconds)
            return True

    def record_task_fail(
        self, worker_id: str, wait_seconds: float, event_id: str | None = None
    ) -> bool:
        """작업이 20% 규칙 또는 기타 사유로 실패했을 때 기록한다."""
        with self._lock:
            if self._is_duplicate_locked(event_id):
                return False
            stats = self._get_or_create_worker_locked(worker_id)
            stats.fail_count += 1
            stats.processed_count += 1
            if wait_seconds > 0:
                stats.total_wait_seconds += float(wait_seconds)
            return True

    def record_queue_overflow(self, worker_id: str) -> None:
        """큐가 10개로 가득 차서 인입이 거절되었을 때 기록한다."""
        with self._lock:
            stats = self._get_or_create_worker_locked(worker_id)
            stats.overflow_count += 1

    def record_p2p_transfer(
        self,
        sender_id: str,
        receiver_id: str,
        task_count: int,
        event_id: str | None = None,
    ) -> bool:
        """P2P 이전이 성공적으로 확정(ACK 수신)되었을 때 기록한다."""
        if task_count <= 0:
            return False
        with self._lock:
            if self._is_duplicate_locked(event_id):
                return False
            sender = self._get_or_create_worker_locked(sender_id)
            receiver = self._get_or_create_worker_locked(receiver_id)

            sender.p2p_transfers_sent += 1
            sender.p2p_tasks_sent += task_count

            receiver.p2p_transfers_received += 1
            receiver.p2p_tasks_received += task_count

            self._total_p2p_events += 1
            self._total_p2p_tasks += task_count
            return True

    def record_reallocation(self, event_id: str | None = None) -> bool:
        """실패한 작업이 다른 Worker에 재할당되었을 때 기록한다."""
        with self._lock:
            if self._is_duplicate_locked(event_id):
                return False
            self._total_reallocations += 1
            return True

    def update_p2p_snapshot(
        self,
        worker_id: str,
        *,
        sent_events: int,
        sent_tasks: int,
        received_events: int,
        received_tasks: int,
    ) -> None:
        """Worker가 보낸 누적값으로 P2P 통계를 갱신한다."""
        values = (sent_events, sent_tasks, received_events, received_tasks)
        if any(value < 0 for value in values):
            raise ValueError("P2P statistics cannot be negative")
        with self._lock:
            stats = self._get_or_create_worker_locked(worker_id)
            stats.p2p_transfers_sent = max(stats.p2p_transfers_sent, sent_events)
            stats.p2p_tasks_sent = max(stats.p2p_tasks_sent, sent_tasks)
            stats.p2p_transfers_received = max(stats.p2p_transfers_received, received_events)
            stats.p2p_tasks_received = max(stats.p2p_tasks_received, received_tasks)
            self._total_p2p_events = sum(
                worker.p2p_transfers_sent for worker in self._workers.values()
            )
            self._total_p2p_tasks = sum(
                worker.p2p_tasks_sent for worker in self._workers.values()
            )

    def get_worker_stats(self, worker_id: str) -> WorkerStats:
        """특정 Worker의 통계 스냅샷을 반환한다."""
        with self._lock:
            stats = self._get_or_create_worker_locked(worker_id)
            return WorkerStats(
                worker_id=stats.worker_id,
                throughput=stats.throughput,
                success_count=stats.success_count,
                fail_count=stats.fail_count,
                overflow_count=stats.overflow_count,
                total_wait_seconds=stats.total_wait_seconds,
                processed_count=stats.processed_count,
                p2p_transfers_sent=stats.p2p_transfers_sent,
                p2p_tasks_sent=stats.p2p_tasks_sent,
                p2p_transfers_received=stats.p2p_transfers_received,
                p2p_tasks_received=stats.p2p_tasks_received,
            )

    def summary(self, total_simulation_seconds: float) -> dict[str, Any]:
        """과제 6대 필수 지표를 포함한 종합 통계 딕셔너리를 반환한다."""
        with self._lock:
            workers_list = [
                self.get_worker_stats(wid).to_dict()
                for wid in sorted(self._workers.keys())
            ]
            total_throughput = sum(w["throughput"] for w in workers_list)
            total_success = sum(w["success_count"] for w in workers_list)
            total_fail = sum(w["fail_count"] for w in workers_list)
            total_processed = sum(
                self._workers[wid].processed_count for wid in self._workers
            )
            total_wait_sum = sum(
                self._workers[wid].total_wait_seconds for wid in self._workers
            )
            overall_avg_wait = (
                total_wait_sum / total_processed if total_processed > 0 else 0.0
            )

            return {
                "total_simulation_time_seconds": round(total_simulation_seconds, 2),
                "total_throughput": total_throughput,
                "total_success_count": total_success,
                "total_fail_count": total_fail,
                "overall_average_wait_seconds": round(overall_avg_wait, 3),
                "total_p2p_events": self._total_p2p_events,
                "total_p2p_tasks_transferred": self._total_p2p_tasks,
                "total_reallocations": self._total_reallocations,
                "worker_statistics": workers_list,
            }

    def format_report_lines(self, total_simulation_seconds: float) -> list[str]:
        """AllDefinedLogs.txt 규격에 맞춰 STAT 이벤트용 로그 문자열 목록을 생성한다."""
        data = self.summary(total_simulation_seconds)
        lines = [
            "==================== Simulation Statistics ====================",
            f"Total Simulation Time: {data['total_simulation_time_seconds']:.2f}s",
            f"Total Completed Tasks: {data['total_throughput']} / 5000",
            f"Total Success: {data['total_success_count']} | Total Fail (20% rule): {data['total_fail_count']}",
            f"Overall Average Wait Time: {data['overall_average_wait_seconds']:.3f}s",
            f"Total P2P Load Balancing Events: {data['total_p2p_events']} ({data['total_p2p_tasks_transferred']} tasks moved)",
            f"Total Fault-Tolerance Reallocations: {data['total_reallocations']}",
            "----------------------------------------------------------------",
        ]
        for w in data["worker_statistics"]:
            lines.append(
                f"Worker {w['worker_id']} | Throughput: {w['throughput']} | "
                f"Success: {w['success_count']} | Fail: {w['fail_count']} | "
                f"AvgWait: {w['average_wait_seconds']:.3f}s | "
                f"P2P (Sent: {w['p2p_transfers_sent']}, Recv: {w['p2p_transfers_received']})"
            )
        lines.append("================================================================")
        return lines
