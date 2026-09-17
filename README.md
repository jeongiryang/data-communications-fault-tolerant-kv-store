# Fault-Tolerant Key-Value Store

분산 환경에서 Master와 네 개의 Worker가 TCP 소켓으로 통신하며, 동적 작업 분배,
P2P 부하 분산, 실패 작업 재할당을 수행하는 Key-Value Store 시뮬레이터다.

> 현재 구현 중이다. 제출 전 미확정 항목을 실제 실행 결과로 갱신하고 이 문서를
> `Readme.txt`로 변환한다.

## 1. 조원 및 역할

| 이름 | 학번 | 역할 |
| --- | --- | --- |
| 정이량 | 제출 전 입력 | 조장, Master, 공통 규격, 최종 통합, 클라우드 배포 |
| 최길웅 | 제출 전 입력 | Worker 실행, Ready Queue, 작업 처리 |
| 배준희 | 제출 전 입력 | P2P 부하 분산, 로그·통계, 장애 복구 지원 |

## 2. 프로그램 구성

| 구성요소 | 실행 위치 | 역할 |
| --- | --- | --- |
| Master Node | AWS EC2 | 작업 5,000개 생성, Queue 기반 분배, 결과 저장, 실패 재할당, 전체 종료 |
| Worker Node 1~4 | 한 로컬 PC의 독립 Thread | Ready Queue 관리, 작업 처리, 결과 보고, P2P 작업 이전 |
| Common | 모든 노드 | TCP 메시지, 공통 자료구조, 논리 시계 |

- Key: 중복 없는 4자리 16진수 문자열
- Value: 1~100 사이 정수
- Worker Ready Queue: 최대 10개
- 작업 처리: 성공 80%, 실패 20%
- 처리시간: 실제 대기 없이 System Clock에 1~3초 반영
- 통신 지연: 실제 대기 없이 System Clock에 1초 반영

## 3. 실행 환경

- Python 3.11 이상
- Master: Ubuntu Server 26.04 LTS, AWS EC2
- Worker: 한 로컬 PC에서 실행되는 네 개의 독립 Worker Thread
- 통신: Master↔Worker 및 Worker↔Worker TCP socket
- P2P: `127.0.0.1:6001`~`127.0.0.1:6004`의 서로 다른 TCP endpoint

## 4. 설치 및 실행

```bash
python -m venv .venv
```

Windows PowerShell:

```powershell
.venv\Scripts\Activate.ps1
python -m pip install -e .
python -m unittest discover -s tests -v
```

Linux:

```bash
source .venv/bin/activate
python -m pip install -e .
python -m unittest discover -s tests -v
```

Master와 Worker의 최종 실행 명령 및 전체 CLI 옵션은 통합 구현 후 이 절에 확정한다.
IP, 포트, 비밀번호는 소스코드에 고정하지 않고 실행 인자 또는 설정으로 전달한다.

## 5. 동적 작업 분배 알고리즘

알고리즘: **Least-Loaded Queue First**

1. Master가 Worker별 Ready Queue 상태를 수집한다.
2. 남은 Queue 공간이 가장 큰 Worker를 선택한다.
3. Queue가 10개인 Worker는 후보에서 제외한다.
4. 실패 작업은 일반 작업보다 먼저 분배한다.
5. 동률이면 Worker ID 순서 또는 순환 순서로 선택한다.

- 시간 복잡도: 작업 배정 1회당 `O(W)`, `W=4`
- 공간 복잡도: Worker 상태 저장 `O(W)`
- 장점: 구현과 검증이 단순하고 현재 Queue 부하를 직접 반영한다.
- 단점: 상태 보고가 지연되면 실제 Queue 상태와 Master의 정보가 다를 수 있다.

## 6. P2P 부하 분산 알고리즘

알고리즘: **Threshold-Based Capacity-Aware Neighbor Offloading**

1. Worker가 1~3초의 무작위 주기로 예상 대기시간을 계산한다.
2. `Queue 작업 수 × 2초`가 15초를 초과하면 이웃 Worker 상태를 조회한다.
3. 여유 공간이 가장 큰 이웃을 선택한다.
4. 1~3개 사이에서 이전 개수를 정하되 수신 Queue의 남은 공간을 초과하지 않는다.
5. 수신 Worker의 ACK를 받은 작업만 송신 Queue에서 제거한다.
6. 거절, timeout, 연결 오류, ACK 누락 시 작업을 원래 Queue에 보존한다.

- 시간 복잡도: 상태 조회 및 대상 선택 `O(N)`, `N`은 이웃 Worker 수
- 공간 복잡도: 이전 후보 작업 `O(K)`, `1 ≤ K ≤ 3`
- 장점: 과부하 Worker가 Master를 거치지 않고 직접 부하를 줄일 수 있다.
- 단점: 동시에 여러 Worker가 이전을 시도하면 경합이 발생할 수 있어 Queue 잠금과
  중복 Task 추적이 필요하다.

## 7. 장애 처리

Worker가 작업 실패를 보고하면 Master가 해당 Task를 Priority Queue에 넣는다.
재할당 시 직전에 실패한 Worker를 우선 제외하고 Queue 여유가 가장 큰 다른 Worker를
선택한다. 재할당 작업에도 동일한 80:20 성공·실패 규칙을 적용하며, 다시 실패하면
성공할 때까지 Priority Queue에 재등록한다. Task ID와 재시도 횟수로 중복 저장과
작업 유실을 방지한다.

## 8. 로그·통계 및 추가 구현

- 로그 형식: `[clock] NODE | EVENT | STATUS | message`
- STATUS: `INFO`, `SUCCESS`, `FAIL`, `WARN`
- 결과 로그: `Master.txt`, `Worker1.txt`~`Worker4.txt`
- 필수 통계: Worker별 처리량, 성공·실패 횟수, 평균 대기시간, P2P 이벤트 횟수,
  장애 재할당 횟수, System Clock 기준 전체 수행시간
- TCP stream 메시지 경계를 위한 길이 기반 프레이밍
- Queue, 논리 시계, 로그, 통계에 대한 Thread-safe 동기화
- request ID와 task ID 기반 요청·작업 추적
