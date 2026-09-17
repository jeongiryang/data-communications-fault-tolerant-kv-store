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

## 구축 완료 상태 (2026-09-17)

- AWS Free plan과 잔여 크레딧을 확인했다.
- Seoul 리전에 Ubuntu 26.04 LTS `t3.micro`, 8 GiB gp3 Master를 만들었다.
- CPU credit specification을 `standard`로 설정했다.
- SSH 22와 Master 5000은 Worker 장소의 현재 공인 IP `/32`에서만 허용했다.
- 저장소 설치와 `kvstore-master.service` 시작을 `cloud-init`으로 자동화했다.
- 로컬 Worker smoke client의 `REGISTER -> ACK` 외부 연결을 확인했다.
- 공인 IP와 인스턴스 ID 같은 실행 시점 값은 저장소에 기록하지 않는다.

## 권장 네트워크 토폴로지

최종 시연의 기본 구성은 **한 로컬 PC에서 네 Worker를 각각 독립 Thread로 실행**하는
것이다. 각 Worker Thread는 Master에 별도 TCP 연결을 열고, P2P 통신을 위해 서로 다른
loopback 포트에서 TCP server를 연다.

```text
AWS EC2 Master (public IP:5000)
          ^ four independent TCP connections
          |
+---------+ Local Worker PC ----------------------------------+
| Worker1 Thread : 127.0.0.1:6001                             |
| Worker2 Thread : 127.0.0.1:6002                             |
| Worker3 Thread : 127.0.0.1:6003                             |
| Worker4 Thread : 127.0.0.1:6004                             |
| P2P transfers use real TCP sockets between loopback ports   |
+-------------------------------------------------------------+
```

이 구성은 네 Worker가 독립 Thread이고 Worker↔Worker 통신이 TCP라는 필수 조건을
그대로 만족하면서 VPN, 공유기 포트 포워딩, 서로 다른 공인 IP를 요구하지 않는다.
Master에서는 같은 공인 IP에서 들어오는 네 연결을 Worker ID로 구분한다.

팀원 PC 여러 대를 사용하고 싶다면 모두 같은 LAN에 연결하고 각 Worker가 자신의
사설 IP와 고유 포트를 광고하는 방식도 가능하다. 이때만 로컬 방화벽에서 P2P 포트를
허용한다. 서로 다른 외부 네트워크를 연결하는 VPN 구성은 사용하지 않는다.

## 담당자별 시작 조건

### Worker runtime 담당

- 하나의 launcher가 `WorkerNode` 네 개를 각각 독립 Thread로 시작할 수 있게 한다.
- Worker마다 ID와 P2P 포트 6001~6004를 별도로 주입한다.
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
- 같은 원격 IP에서 접속하더라도 Worker ID가 서로 다른 네 등록을 허용한다.
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
