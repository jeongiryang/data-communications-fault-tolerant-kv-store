# 공통 TCP 프로토콜 v0.1

이 문서는 Master·Worker·P2P 구현이 공유하는 초기 계약이다. 변경이 필요하면
관련 담당자와 합의하고 버전 및 테스트를 함께 갱신한다.

## 프레임

TCP는 메시지 경계를 보존하지 않으므로 `recv()` 한 번을 메시지 한 개로 취급하지
않는다.

```text
+----------------------+---------------------------+
| payload length       | UTF-8 JSON payload        |
| 4-byte unsigned int  | exactly payload length    |
| network byte order   | bytes                     |
+----------------------+---------------------------+
```

- 최대 JSON payload 크기: 1 MiB
- JSON 최상위 값: object
- 프로토콜 버전: `1`
- 연결 timeout 권장값: 5초

## 공통 envelope

```json
{
  "version": 1,
  "type": "REGISTER",
  "sender_id": "worker-1",
  "request_id": "unique-request-id",
  "logical_clock": 0.0,
  "payload": {}
}
```

- `request_id`는 요청·응답과 중복 전송을 추적한다.
- 응답 `ACK` 또는 `ERROR`는 요청과 같은 `request_id`를 사용한다.
- Task는 `task_id`로 최종 저장, 재시도, P2P 이전 중복을 추적한다.
- `logical_clock`은 송신 시점에 송신자가 알고 있는 논리 시각이다.

## 메시지 종류

| type | 방향 | 목적 |
| --- | --- | --- |
| `REGISTER` | Worker -> Master | Worker ID, P2P endpoint, Queue 크기 등록 |
| `TASK` | Master -> Worker | 일반 또는 재할당 작업 전달 |
| `QUEUE_STATUS` | Worker -> Master | Queue 크기와 처리 상태 보고 |
| `RESULT_SUCCESS` | Worker -> Master | 성공 결과 보고 |
| `RESULT_FAIL` | Worker -> Master | 실패 또는 Queue overflow 보고 |
| `P2P_STATUS_REQUEST` | Worker -> Worker | 이웃 Queue 상태 요청 |
| `P2P_STATUS_RESPONSE` | Worker -> Worker | Queue 상태 응답 |
| `P2P_TRANSFER` | Worker -> Worker | 작업 1~3개 이전 요청 |
| `ACK` | 양방향 | 등록·이전 등 요청 완료 확인 |
| `TERMINATE` | Master -> Worker | 정상 종료 요청 |
| `ERROR` | 양방향 | 잘못된 요청 또는 처리 오류 |

## Task payload

```json
{
  "task": {
    "task_id": "task-0001",
    "key": "a3f7",
    "value": 42,
    "attempt": 0,
    "previous_worker_id": null,
    "enqueued_at": 0.0
  },
  "priority": false
}
```

재할당 때 `attempt`를 증가시키고 `previous_worker_id`를 기록한다. Master는 바로
직전에 실패한 Worker를 우선 제외한다.

## P2P 안전 규칙

1. 송신 Worker는 후보 Task를 잠금으로 예약하되 아직 Queue에서 제거하지 않는다.
2. 수신 Worker는 남은 Queue 용량 안에서만 Task를 삽입한다.
3. 수신 Worker는 삽입된 `task_id` 목록을 `ACK`로 보낸다.
4. 송신 Worker는 ACK에 포함된 Task만 제거한다.
5. timeout, 연결 오류, 거절, ACK 누락 시 예약을 해제하고 원래 Queue에 보존한다.

논리 통신 지연 1초는 protocol 함수가 자동 증가시키지 않는다. 실제 상태 변경이
확정되는 호출 지점에서 공통 `LogicalClock`을 정확히 한 번 증가시킨다. 메시지 수신
노드는 먼저 `clock.observe(message.logical_clock)`으로 원격 시각을 반영한다. 최종
Master는 전체 시뮬레이션 시각의 권위자이며, Worker의 처리시간을 담은 결과 메시지를
관측해 절대 뒤로 가지 않는 시각을 유지한다.
