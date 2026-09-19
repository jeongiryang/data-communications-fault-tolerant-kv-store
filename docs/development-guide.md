# 개발 환경과 공통 기반 가이드

이 문서는 개발 중인 팀원을 위한 빠른 시작과 공통 규격의 진입점이다. 제출용 설명은
루트 `README.md`에서 관리하며, 개발 세부사항은 제출 문서에 섞지 않는다.

## 현재 제공되는 기반

팀원이 같은 규격으로 개발을 시작할 수 있도록 공통 메시지 모델, TCP 프레이밍,
논리 시계, Master 등록 stub과 Worker smoke client가 포함되어 있다.

과제의 최종 구현·검증 기준은 [`assignment-requirements.md`](assignment-requirements.md)를
따른다. 개발 전에 [`../AGENTS.md`](../AGENTS.md)와 담당 Issue도 함께 확인한다.

## 빠른 시작

Python 3.11 이상에서 외부 런타임 의존성 없이 실행할 수 있다.

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s tests -v
python -m kvstore.master.stub --host 127.0.0.1 --port 5000
```

다른 터미널에서 Worker 등록 smoke test를 실행한다.

```powershell
$env:PYTHONPATH = "src"
python -m kvstore.worker.smoke_client `
  --worker-id worker-1 `
  --master-host 127.0.0.1 `
  --master-port 5000 `
  --p2p-host 127.0.0.1 `
  --p2p-port 6001
```

현재 stub은 `REGISTER -> ACK` 연결만 검증한다. 실제 Master 스케줄러와 Worker
처리 루프는 각 담당 브랜치에서 공통 모듈을 사용해 구현한다.

실제 Master 스케줄러와 연결할 때는 launcher 한 번으로 Worker 4개를 시작한다.

```powershell
$env:PYTHONPATH = "src"
python -m kvstore.worker.launcher `
  --master-host <Master 주소> `
  --master-port 5000
```

네 Worker는 한 프로세스 안의 독립 Thread로 실행되며 하나의 논리 시계를 공유한다.
P2P 포트는 기본적으로 `6001`부터 `6004`까지 사용한다.

## 합의된 기반

- Python 3.11 표준 라이브러리
- TCP 메시지 형식: 4바이트 big-endian payload 길이 + UTF-8 JSON
- 프로토콜 버전: `1`
- Master 기본 포트: `5000`
- Worker P2P 권장 포트: `6001`~`6004`
- Worker Ready Queue 크기: `10`
- 네트워크 주소와 포트는 CLI 또는 설정으로 주입하며 코드에 개인 IP를 넣지 않는다.

## Master 실행

```powershell
$env:PYTHONPATH = "src"
python -m kvstore.master.runtime --host 0.0.0.0 --port 5000
```

Master는 Worker 4개 등록이 끝난 뒤 5,000개 작업 분배를 시작한다. 테스트가 아닌 실제
실행에서는 작업 수를 변경하지 않는다.

## 개발 부록

- [공통 TCP 프로토콜](protocol-v0.1.md)
- [오너 선행 작업과 팀 통합 기준](owner-bootstrap.md)
- [AWS Master 최소 배포 가이드](aws-deployment.md)
