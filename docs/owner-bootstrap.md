# 오너 선행 작업과 팀 통합 기준

## 지금 팀원에게 고정해서 전달할 값

| 항목 | 합의값 |
| --- | --- |
| 구현 언어 | Python 3.11 이상, 표준 라이브러리 우선 |
| Master 기본 포트 | TCP 5000 |
| Worker P2P 포트 | TCP 6001, 6002, 6003, 6004 |
| Ready Queue 크기 | 10 |
| 메시지 프레임 | 4바이트 길이 + UTF-8 JSON |
| 메시지 계약 | `docs/protocol-v0.1.md` |
| 로컬 검증 | `Master registration stub` 사용 |

포트와 주소는 기본값일 뿐이며 CLI/config로 주입한다. 개인 공인 IP는 문서나 코드에
커밋하지 않는다.

## 권장 네트워크 토폴로지

최종 시연은 네 Worker가 같은 LAN에서 서로의 사설 IP에 접속하도록 구성한다.

```text
AWS EC2 Master (public IP:5000)
          ^
          | outbound TCP from Workers
          |
same LAN: Worker1:6001 <-> Worker2:6002 <-> Worker3:6003 <-> Worker4:6004
```

이 방식은 가정용 공유기 NAT를 넘어 Worker가 서로 접속해야 하는 문제를 피한다.
서로 다른 외부 네트워크에서 개발할 때는 localhost 테스트를 사용한다. VPN overlay를
사용하려면 과제에서 허용되는지 교수자에게 먼저 확인한다.

## 담당자별 시작 조건

### Worker runtime 담당

- `Task`, `Message`, `RegisterPayload`, `WorkerStatus`를 재정의하지 않는다.
- `send_message`와 `receive_message`로 Master 통신을 구현한다.
- Queue 구현이 P2P 담당자에게 안전한 조회·예약·ACK 후 제거 인터페이스를 제공한다.

### P2P·로그·통계 담당

- `P2P_STATUS_*`, `P2P_TRANSFER`, `ACK` envelope를 사용한다.
- ACK 전에는 송신 Queue에서 Task를 삭제하지 않는다.
- Worker runtime의 Queue 인터페이스가 부족하면 중복 Queue를 만들지 말고 PR에
  필요한 인터페이스 변경을 명시한다.

### Master·통합 담당

- registration stub을 실제 Master 서버로 점진적으로 교체한다.
- Worker 4개가 모두 등록된 뒤 분배를 시작한다.
- 실패 작업은 Task ID 기준 Priority Queue에서 추적하고 직전 실패 Worker를 제외한다.
- AWS 배포 전에 localhost Worker 4개로 전체 흐름을 먼저 통과시킨다.

## 통합 게이트

PR마다 다음 항목을 확인한다.

1. `python -m unittest discover -s tests -v` 성공
2. 개인 IP·토큰·키·절대경로 없음
3. 공통 프레임 및 모델을 중복 구현하지 않음
4. 정상 흐름과 담당 실패 흐름 테스트 포함
5. 실행 방법, 테스트 결과, 미완성 항목을 PR 본문에 기록
