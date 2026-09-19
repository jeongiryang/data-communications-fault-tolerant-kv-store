# AWS Master 최소 배포 가이드

이미 구축된 EC2에서 전체 프로그램을 직접 실행하려면
[`manual-test-guide.md`](manual-test-guide.md)를 따른다. 이 문서는 신규 AWS 환경을
구축하거나 네트워크 설정을 점검할 때 사용한다.

과제에는 외부 클라우드 Master가 필수다. 이 가이드는 복잡한 관리 서비스를 추가하지
않고 EC2 한 대에 Master를 배포하는 최소 구성을 사용한다.

## 선택한 구성

- Account: 신규 AWS **Free plan**; Paid plan으로 업그레이드하지 않음
- Region: `ap-northeast-2` (Seoul)
- EC2: `t3.micro` x86_64
- OS: Ubuntu Server 26.04 LTS
- CPU credit specification: `standard` (무제한 크레딧 추가 사용 방지)
- Master application port: TCP `5000`
- 관리: SSH 또는 EC2 Instance Connect
- 데이터베이스·Load Balancer·RDS·NAT Gateway: 사용하지 않음

신규 계정은 가입할 때 Free plan을 선택한다. 이 플랜은 가입 크레딧을 사용하는 동안
요금이 청구되지 않으며, 가입 후 6개월 또는 크레딧 소진 중 먼저 도달하는 시점에
계정이 자동 종료된다. **Paid plan으로 전환하지 않는다.** 기존 Paid plan 계정에서는
비용이 발생하지 않는다고 보장할 수 없으므로 이 과제 전용 신규 Free plan 계정을
권장한다.

EC2와 공인 IPv4 사용량도 크레딧을 소모한다. 공인 IPv4는 시간당 과금 항목이므로
Elastic IP를 별도로 만들지 않고, 시연할 때만 EC2의 자동 할당 public IPv4를 사용한다.
인스턴스를 중지하면 주소가 바뀔 수 있으므로 실행할 때 Worker 설정에 현재 주소를
주입한다.

공식 참고 자료:

- [AWS Budgets 템플릿](https://docs.aws.amazon.com/cost-management/latest/userguide/budget-templates.html)
- [AWS Free plan 선택과 종료 조건](https://docs.aws.amazon.com/awsaccountbilling/latest/aboutv2/free-tier-plans.html)
- [EC2 Security Group 규칙 예시](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/security-group-rules-reference.html)
- [Public IPv4 가격](https://aws.amazon.com/vpc/pricing/)

## 1. 계정 안전 설정

1. 신규 계정 가입 화면에서 **Free plan**을 선택한다.
2. Root 계정 MFA를 활성화한다.
3. AWS Budgets에서 Zero Spend 또는 작은 월별 예산 알림을 만든다.
4. Paid plan 업그레이드, AWS Organizations 가입, 유료 Marketplace 상품 구매를 하지
   않는다.
5. 장기 Access Key를 저장소나 팀 채팅에 공유하지 않는다.

## 2. EC2와 Security Group

인스턴스를 만든 뒤 다음 inbound 규칙만 둔다.

| 용도 | 프로토콜/포트 | Source |
| --- | --- | --- |
| SSH | TCP 22 | 오너의 현재 공인 IP `/32` |
| Master | TCP 5000 | Worker PC가 있는 장소의 공인 IP `/32` |

- SSH `0.0.0.0/0`는 사용하지 않는다.
- Worker PC 측 공인 IP가 바뀌면 Security Group source를 갱신한다.
- Master 소켓은 EC2에서 `0.0.0.0:5000`에 bind한다.
- Worker에는 EC2의 공인 IP 또는 DNS를 `--master-host`로 전달한다.

위 표는 보안을 우선한 기본 권장 설정이다. 현재 과제 시연 환경은 연구실·집·노트북
어디서든 접속할 수 있게 Master TCP 5000만 `0.0.0.0/0`으로 열어 두었다. SSH 22는
서울 리전 EC2 Instance Connect 주소만 허용한다. Master에는 인증 기능이 없으므로
시연하지 않을 때는 인스턴스를 중지하고, 제출이 끝나면 5000 규칙을 제거한다.

Elastic IP는 만들지 않는다. 시연이 끝나면 EC2를 즉시 중지하고, 최종 제출이 끝나면
인스턴스를 종료한다. Free plan의 남은 기간과 크레딧은 Billing 화면에서 매번 확인한다.

## 3. 서버 준비

```bash
sudo apt-get update
sudo apt-get install -y git python3 python3-venv
sudo mkdir -p /opt/kvstore
sudo chown "$USER":"$USER" /opt/kvstore
git clone https://github.com/jeongiryang/data-communications-fault-tolerant-kv-store.git /opt/kvstore
cd /opt/kvstore
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/python -m unittest discover -s tests -v
```

공통 연결 확인 단계에서는 다음 stub을 실행한다.

```bash
cd /opt/kvstore
.venv/bin/python -m kvstore.master.stub --host 0.0.0.0 --port 5000
```

로컬 PC에서 `smoke_client`를 실행해 `REGISTER -> ACK`가 성공하는지 확인한다.
이 검증 후 실제 Master entry point로 교체한다.

Ubuntu 26.04의 첫 부팅에서는 패키지 인덱스와 Python 구성요소를 갱신하므로 자동
설치 완료까지 약 3~5분이 걸릴 수 있다. EC2 상태 검사 통과만으로 배포 완료를
판단하지 않고, `cloud-init` 완료와 `REGISTER -> ACK`를 최종 기준으로 사용한다.

## 4. 배포 전 네트워크 점검

- EC2에서 Master가 `0.0.0.0:5000`을 listen하는가
- Worker PC에서 EC2 public IP의 TCP 5000에 연결되는가
- Security Group source가 Worker 장소의 현재 공인 IP인가
- 한 PC에서 Worker 4개를 실행할 경우 P2P 주소가 `127.0.0.1`, 포트가
  6001~6004로 서로 다른가
- 여러 PC의 같은 LAN에서 실행할 경우 광고한 사설 IP와 P2P 포트에 서로 접근 가능한가
- 코드·설정·로그에 개인 IP, PEM key, AWS credential이 포함되지 않았는가

## 5. 시연 후 정리

1. 최종 로그를 내려받는다.
2. EC2 인스턴스를 종료한다.
3. EC2의 상태가 `stopped` 또는 `terminated`인지 확인한다.
4. Elastic IP, NAT Gateway, Load Balancer가 생성되지 않았는지 확인한다.
5. Billing dashboard에서 Free plan 잔여 기간과 크레딧을 확인한다.
