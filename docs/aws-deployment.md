# AWS Master 최소 배포 가이드

과제에는 외부 클라우드 Master가 필수다. 이 가이드는 복잡한 관리 서비스를 추가하지
않고 EC2 한 대에 Master를 배포하는 최소 구성을 사용한다.

## 선택한 구성

- Region: `ap-northeast-2` (Seoul)
- EC2: `t3.micro` x86_64
- OS: Ubuntu Server 24.04 LTS
- Master application port: TCP `5000`
- 관리: SSH 또는 EC2 Instance Connect
- 데이터베이스·Load Balancer·RDS·NAT Gateway: 사용하지 않음

계정별 Free Tier 조건이 다를 수 있으므로 인스턴스 생성 화면의 예상 요금을 확인한다.
공인 IPv4와 Elastic IP도 과금될 수 있다.

공식 참고 자료:

- [AWS Budgets 템플릿](https://docs.aws.amazon.com/cost-management/latest/userguide/budget-templates.html)
- [EC2 Security Group 규칙 예시](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/security-group-rules-reference.html)
- [Elastic IP 주소와 과금](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/elastic-ip-addresses-eip.html)

## 1. 계정 안전 설정

1. Root 계정 MFA를 활성화한다.
2. AWS Budgets에서 Zero Spend 또는 작은 월별 예산 알림을 만든다.
3. 장기 Access Key를 저장소나 팀 채팅에 공유하지 않는다.

## 2. EC2와 Security Group

인스턴스를 만든 뒤 다음 inbound 규칙만 둔다.

| 용도 | 프로토콜/포트 | Source |
| --- | --- | --- |
| SSH | TCP 22 | 오너의 현재 공인 IP `/32` |
| Master | TCP 5000 | 각 Worker 실행 장소의 공인 IP `/32` |

- SSH `0.0.0.0/0`는 사용하지 않는다.
- 팀원 공인 IP가 바뀌면 Security Group source를 갱신한다.
- Master 소켓은 EC2에서 `0.0.0.0:5000`에 bind한다.
- Worker에는 EC2의 공인 IP 또는 DNS를 `--master-host`로 전달한다.

중지·시작 후 주소가 바뀌지 않아야 하면 Elastic IP를 연결할 수 있지만, 사용 중인
공인 IPv4를 포함해 요금이 발생할 수 있다. 시연이 끝나면 인스턴스와 불필요한 IP를
정리한다.

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

## 4. 배포 전 네트워크 점검

- EC2에서 Master가 `0.0.0.0:5000`을 listen하는가
- Worker PC에서 EC2 public IP의 TCP 5000에 연결되는가
- Security Group source가 Worker 장소의 현재 공인 IP인가
- Worker가 Master에 광고하는 P2P 주소가 다른 Worker에서 접근 가능한 LAN IP인가
- Worker P2P 포트 6001~6004를 로컬 방화벽이 허용하는가
- 코드·설정·로그에 개인 IP, PEM key, AWS credential이 포함되지 않았는가

## 5. 시연 후 정리

1. 최종 로그를 내려받는다.
2. EC2 인스턴스를 종료한다.
3. Elastic IP를 사용했다면 연결 상태와 계속 필요한지 확인하고 불필요하면 해제한다.
4. Billing dashboard와 Budget 알림을 확인한다.
