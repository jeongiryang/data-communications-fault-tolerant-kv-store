# AWS Master와 로컬 Worker 수동 테스트 가이드

이 문서는 처음 시연하는 사람이 AWS 콘솔 로그인부터 전체 실행과 종료까지 그대로
따라 할 수 있게 작성한 절차서다. 기본 구성은 AWS EC2에서 Master 1개를 실행하고,
한 대의 Windows PC에서 Worker 4개를 각각 독립 Thread로 실행하는 방식이다.

## 목차

- [가장 간단한 실행 명령](#quick-start)

### 역할별 바로가기

- [Master 노드 담당자가 읽을 부분(정이량)](#master-role)
- [Worker 노드 담당자가 읽을 부분(최길웅, 배준희)](#worker-role)

### 전체 절차

- [0. 역할과 실행 순서](#step-0)
- [1. 테스트 전 준비](#step-1)
- [2. AWS 콘솔에서 EC2 시작](#step-2)
- [3. AWS 서버 터미널 접속](#step-3)
- [4. AWS 최신 코드와 테스트 확인](#step-4)
- [5. AWS Master 실행](#step-5)
- [6. 로컬 PC 저장소 준비](#step-6)
- [7. 로컬 Worker 4개 실행](#step-7)
- [8. 성공 여부와 로그 확인](#step-8)
- [9. 테스트 후 EC2 중지](#step-9)
- [10. 문제 해결](#step-10)
- [11. 시연 영상 순서](#step-11)

<a id="quick-start"></a>

## 가장 간단한 실행 명령

아래 명령은 현재 터미널 위치와 관계없이 컴퓨터 안에서 실행 스크립트를 찾는다. 찾은
저장소를 `main` 최신 상태로 갱신하고, 설치와 테스트를 거쳐 Master 또는 Worker를
실행한다. 저장소 폴더로 직접 이동하거나 `git pull`을 따로 입력할 필요가 없다.

**Master 노드(오너):**

AWS EC2 Instance Connect 터미널에서 다음 한 줄을 실행한다.

```bash
bash "$(find /opt /home/ubuntu -type f -name run-master.sh -print -quit 2>/dev/null)"
```

Master가 `Worker 연결을 기다립니다`라고 출력하면 AWS 화면의 현재 퍼블릭 IPv4 주소를
팀원에게 전달한다.

**Worker 노드(팀원):**

Windows PowerShell에서 `<AWS-IP>`를 전달받은 실제 주소로 바꿔 다음 한 줄을 실행한다.
PowerShell을 어느 폴더에서 열어도 된다.

```powershell
$s = Get-ChildItem $HOME -Filter run-workers.cmd -Recurse -File -ErrorAction SilentlyContinue | Select-Object -First 1; if (-not $s) { throw "Worker 저장소를 찾지 못했습니다." }; $r = Split-Path (Split-Path $s.FullName -Parent) -Parent; git -C $r switch main; if ($LASTEXITCODE) { throw "main 브랜치 전환에 실패했습니다." }; git -C $r pull --ff-only origin main; if ($LASTEXITCODE) { throw "최신 코드 반영에 실패했습니다." }; & "$r\scripts\run-workers.cmd" <AWS-IP>
```

예를 들어 주소가 `12.34.56.78`이면 마지막 부분만 다음과 같이 입력한다.

```powershell
$s = Get-ChildItem $HOME -Filter run-workers.cmd -Recurse -File -ErrorAction SilentlyContinue | Select-Object -First 1; if (-not $s) { throw "Worker 저장소를 찾지 못했습니다." }; $r = Split-Path (Split-Path $s.FullName -Parent) -Parent; git -C $r switch main; if ($LASTEXITCODE) { throw "main 브랜치 전환에 실패했습니다." }; git -C $r pull --ff-only origin main; if ($LASTEXITCODE) { throw "최신 코드 반영에 실패했습니다." }; & "$r\scripts\run-workers.cmd" 12.34.56.78
```

Master 스크립트와 Worker 스크립트는 저장소 최신화, 실행 환경 준비, 설치, 전체 테스트,
로그 폴더 생성과 실행을 차례대로 처리한다. Worker 스크립트는 마지막에 Worker 4개의
정상 종료도 확인한다. 아래 절차는 처음 준비하거나 오류가 생겼을 때만 읽으면 된다.

<a id="step-0"></a>

## 0. 먼저 역할과 실행 순서 확인하기

한 번의 테스트에서는 **오너 한 명이 AWS Master를 실행하고, 팀원 한 명이 자신의
PC에서 Worker 4개를 모두 실행**한다. 여러 팀원이 동시에 Worker launcher를 실행하면
Worker ID와 연결 수가 겹칠 수 있으므로 한 명만 실행한다.

| 담당 | 진행할 절 | 할 일 |
| --- | --- | --- |
| 오너 | 2~5절, 9절 | EC2 시작, 현재 공인 IP 전달, Master 실행, 종료 후 EC2 중지 |
| Worker 실행 팀원 | 1절, 6~8절 | Python·Git 확인, 로컬 코드 준비, Worker 4개 실행, 로그 확인 |

실행 순서는 다음과 같다.

1. 팀원이 1절과 6절을 진행해 로컬 실행 환경을 준비한다.
2. 오너가 2~5절을 진행하고 Master에 `0.0.0.0:5000에서 Worker 연결을 기다립니다`가 표시됐는지 확인한다.
3. 오너가 현재 EC2 퍼블릭 IPv4 주소와 함께 “Master 준비 완료”라고 팀원에게 알린다.
4. 팀원이 7절 명령의 `<AWS-IP>`를 전달받은 주소로 바꾸고 Worker를 실행한다.
5. 오너와 팀원이 각각 8절의 성공 기준을 확인한다.
6. 오너가 9절에 따라 EC2를 중지한다.

팀원은 AWS 계정에 로그인하거나 EC2를 직접 조작할 필요가 없다. 오너가 전달할 정보는
현재 EC2 퍼블릭 IPv4 주소 하나다.

<a id="master-role"></a>

### Master 노드 담당자가 읽을 부분(정이량)

Master 노드 담당자는 다음 절만 순서대로 읽으면 된다.

1. [AWS 콘솔에서 EC2 시작](#step-2)
2. [AWS 서버 터미널 접속](#step-3)
3. [AWS 최신 코드와 테스트 확인](#step-4)
4. [AWS Master 실행](#step-5)
5. [Master 성공 여부와 로그 확인](#master-result)
6. [테스트 후 EC2 중지](#step-9)

Master를 실행한 뒤에는 현재 EC2 퍼블릭 IPv4 주소를 Worker 담당자에게 전달한다.

<a id="worker-role"></a>

### Worker 노드 담당자가 읽을 부분(최길웅, 배준희)

Worker 노드 담당자는 다음 절만 순서대로 읽으면 된다.

1. [테스트 전 준비](#step-1)
2. [로컬 PC 저장소 준비](#step-6)
3. [로컬 Worker 4개 실행](#step-7)
4. [Worker 성공 여부와 로그 확인](#worker-result)

Worker 담당자는 AWS 콘솔에 로그인하지 않는다. Master 담당자가 “준비 완료”라고 알리고
현재 공인 IP를 전달한 뒤에만 Worker 실행 명령을 입력한다.

<a id="step-1"></a>

## 1. 테스트 전에 준비할 것

- AWS 계정 로그인 정보
- Python 3.11 이상이 설치된 Windows PC
- Git이 설치된 Windows PC
- 이 저장소를 내려받을 수 있는 인터넷 연결
- AWS와 Worker PC에서 TCP 5000 통신이 가능한 네트워크

현재 EC2 보안 그룹은 장소가 달라져도 Worker가 접속할 수 있도록 TCP 5000을
외부에서 접근 가능하게 설정했다. 시연하지 않을 때는 EC2를 중지한다.

PowerShell에서 다음 명령으로 Python과 Git 설치 여부를 확인한다.

```powershell
py --version
git --version
```

두 명령 모두 버전이 출력되어야 한다. `py` 명령이 없다면 Python을 먼저 설치하고,
설치 화면에서 `Add Python to PATH`를 선택한다.

<a id="step-2"></a>

## 2. AWS 콘솔에서 EC2 시작하기

1. 웹 브라우저에서 [AWS Management Console](https://console.aws.amazon.com/)을 연다.
2. AWS 계정으로 로그인한다.
3. 화면 오른쪽 위 리전을 **아시아 태평양(서울)**로 선택한다.
4. 화면 위 검색창에 `EC2`를 입력하고 **EC2** 서비스를 누른다.
5. 왼쪽 메뉴에서 **인스턴스 > 인스턴스**를 누른다.
6. 목록에서 이름이 `data-communications-master`인 인스턴스를 선택한다.
7. 화면 위 **인스턴스 상태** 버튼을 누르고 **인스턴스 시작**을 선택한다.
8. 인스턴스 상태가 `실행 중`이 될 때까지 기다린다.
9. 아래쪽 **상태 및 경보** 탭에서 상태 검사가 통과할 때까지 기다린다.

인스턴스를 중지했다가 다시 시작하면 공인 IP가 바뀔 수 있다. 인스턴스 요약에
표시된 **퍼블릭 IPv4 주소**를 복사해서 메모한다. 이 주소는 뒤에서 `<AWS-IP>`라고
표시한다.

<a id="step-3"></a>

## 3. AWS 서버 터미널 열기

1. `data-communications-master` 인스턴스가 선택된 상태에서 화면 위 **연결**을 누른다.
2. **EC2 Instance Connect** 탭을 선택한다.
3. 사용자 이름이 `ubuntu`인지 확인한다.
4. **연결** 버튼을 누른다.
5. 새 브라우저 탭에 `ubuntu@...:~$` 프롬프트가 보이면 접속된 것이다.

연결에 실패하면 인스턴스가 실행 중인지, 리전이 서울인지 먼저 확인한다. 계속
실패하면 보안 그룹의 SSH 규칙이 서울 리전 EC2 Instance Connect 주소를 허용하는지
확인한다.

<a id="step-4"></a>

## 4. AWS에서 최신 코드와 테스트 확인하기

EC2 Instance Connect 터미널에서 다음 명령을 순서대로 실행한다.

```bash
cd /opt/kvstore
git switch main
git pull --ff-only origin main
.venv/bin/pip install -e .
.venv/bin/python -m unittest discover -s tests -q
```

마지막에 다음과 같이 출력되면 AWS의 코드 테스트가 통과한 것이다.

```text
Ran 50 tests in ...
OK
```

테스트 개수는 코드가 추가되면 늘어날 수 있다. 중요한 것은 마지막 결과가 `OK`인
것이다.

<a id="step-5"></a>

## 5. AWS에서 Master 실행하기

평소에는 문서 맨 위의 [가장 간단한 실행 명령](#quick-start)을 사용한다. 다음 명령은
저장소 위치를 이미 알고 있을 때만 사용한다.

```bash
cd /opt/kvstore && git pull --ff-only origin main && bash scripts/run-master.sh
```

아래 명령은 스크립트를 사용하지 않고 직접 실행해야 할 때만 사용한다.

초기 구축 때 사용했던 등록 확인용 서버가 자동 실행될 수 있으므로 먼저 중지한다.

```bash
sudo systemctl disable --now kvstore-master.service
```

Master 로그를 저장할 폴더를 만들고 실제 Master를 실행한다.

```bash
cd /opt/kvstore
mkdir -p /home/ubuntu/kvstore-manual-test-logs
.venv/bin/python -m kvstore.master.runtime \
  --host 0.0.0.0 \
  --port 5000 \
  --log-dir /home/ubuntu/kvstore-manual-test-logs
```

Master는 Worker 4개가 연결될 때까지 기다린다. 이 터미널 탭은 닫지 않는다.
`중복 없는 작업 5000개를 생성했습니다`와 `0.0.0.0:5000에서 Worker 연결을 기다립니다` 로그가 보이면 로컬 Worker를
실행할 준비가 된 것이다.

<a id="step-6"></a>

## 6. 로컬 PC에 저장소 준비하기

### 저장소가 이미 있는 경우

새 PowerShell을 열고 저장소 폴더로 이동한다.

```powershell
cd "저장소가 있는 폴더\data-communications-fault-tolerant-kv-store"
git switch main
git pull --ff-only origin main
```

### 저장소가 없는 PC에서 처음 실행하는 경우

원하는 작업 폴더에서 다음 명령을 실행한다.

```powershell
git clone https://github.com/jeongiryang/data-communications-fault-tolerant-kv-store.git
cd data-communications-fault-tolerant-kv-store
git switch main
```

가상환경을 만들고 코드를 설치한다. PowerShell 실행 정책 문제를 피하기 위해
가상환경을 활성화하지 않고 Python 실행 파일을 직접 사용한다.

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m unittest discover -s tests -q
```

마지막 결과가 `OK`인지 확인한다.

<a id="step-7"></a>

## 7. 로컬에서 Worker 4개 실행하기

평소에는 문서 맨 위의 [가장 간단한 실행 명령](#quick-start)을 사용한다. 다음 명령은
저장소 폴더를 이미 열어 둔 경우에만 사용한다.

```powershell
.\scripts\run-workers.cmd <AWS-IP>
```

문서 맨 위의 가장 간단한 명령은 최신 코드를 반영한 뒤 이 스크립트를 실행한다. 이
스크립트는 설치와 테스트부터 Worker 4개 실행, 로그 저장, 정상 종료 확인까지 처리한다.
로그는 실행 시각별로 `manual-test-logs\날짜-시간` 폴더에 저장된다. 아래 명령은
스크립트를 사용하지 않고 직접 실행해야 할 때만 사용한다.

먼저 이번 실행의 Worker 로그 폴더를 만든다.

```powershell
New-Item -ItemType Directory -Force .\manual-test-logs
```

다음 명령에서 `<AWS-IP>`를 2단계에서 복사한 실제 퍼블릭 IPv4 주소로 바꾼다.
꺾쇠괄호까지 그대로 입력하면 안 된다.

```powershell
.\.venv\Scripts\python.exe -m kvstore.worker.launcher `
  --master-host <AWS-IP> `
  --master-port 5000 `
  --p2p-host 127.0.0.1 `
  --p2p-base-port 6001 `
  --log-dir .\manual-test-logs
```

예를 들어 AWS 화면에 `12.34.56.78`이 표시됐다면 다음과 같이 입력한다.

```powershell
.\.venv\Scripts\python.exe -m kvstore.worker.launcher `
  --master-host 12.34.56.78 `
  --master-port 5000 `
  --p2p-host 127.0.0.1 `
  --p2p-base-port 6001 `
  --log-dir .\manual-test-logs
```

이 명령 하나가 Worker1~Worker4를 각각 독립 Thread로 실행한다. P2P 통신은 다음
TCP 주소를 사용한다.

| Worker | P2P 주소 |
| --- | --- |
| Worker1 | `127.0.0.1:6001` |
| Worker2 | `127.0.0.1:6002` |
| Worker3 | `127.0.0.1:6003` |
| Worker4 | `127.0.0.1:6004` |

터미널에는 연결, 진행률, 통계와 종료처럼 시연에 필요한 로그를 항상 출력한다. 반복되는
20% 작업 실패는 처음 5건과 이후 100건마다, P2P ACK는 처음 5건과 이후 10건마다,
Queue 경고는 처음 3건만 보여 준다. 연결 실패 같은 오류는 항상 출력한다. 생략된 내용을
포함한 작업별 상세 로그는 `Worker1.txt`~`Worker4.txt`에 모두 저장된다. 논리 시간만
증가시키므로 실제로 1~3초씩 기다리지는 않는다.

진행률은 100개 단위로 표시한다. 터미널의 왼쪽 시간은 `[5556.41초]`처럼 누적 초를
바로 보여 주고, 로그 파일에는 과제 형식에 맞춰 `[5556.41]`로 저장한다. 터미널 로그
사이의 시간이 크게 뛰는 것은 그 사이 작업별 상세 출력을 생략했기 때문이며, 논리 시계
자체는 모든 처리와 통신 시간을 계속 반영한다.

<a id="step-8"></a>

## 8. 테스트 성공 여부 확인하기

긴 로그를 처음부터 읽을 필요는 없다. 오너와 Worker 실행 팀원이 아래의 짧은 확인
명령만 각각 실행하면 된다.

<a id="master-result"></a>

### 오너가 AWS에서 확인할 것

모든 작업이 끝나면 AWS Master 터미널에 다음 내용이 출력되어야 한다.

```text
전체 완료 작업: 5000 / 5000
작업 5000개를 저장하고 정상 종료했습니다
```

P2P와 장애 복구가 실제로 발생했는지도 Master 통계에서 확인한다.

- `P2P 부하 분산` 횟수가 0보다 큰가
- `장애 재할당` 횟수가 0보다 큰가
- Worker별 성공 합계가 5,000인가

Master 로그에서 필수 결과만 다시 출력하려면 AWS 터미널에서 다음 명령을 실행한다.

```bash
grep -E "전체 완료 작업|전체 성공|P2P 부하 분산|장애 재할당|TERMINATE" /home/ubuntu/kvstore-manual-test-logs/Master.txt
```

다음 조건을 모두 만족하면 Master 실행은 성공이다.

- `전체 완료 작업: 5000 / 5000`
- `전체 성공: 5000`
- P2P 이벤트와 장애 재할당이 0보다 큼
- 마지막에 `TERMINATE | SUCCESS | 작업 5000개를 저장하고 정상 종료했습니다.`가 있음

<a id="worker-result"></a>

### Worker 실행 팀원이 Windows에서 확인할 것

로컬 PowerShell에서 Worker 로그 파일을 확인한다.

```powershell
Get-ChildItem .\manual-test-logs\Worker*.txt
Get-Content .\manual-test-logs\Worker1.txt -Tail 10
Select-String -Path .\manual-test-logs\Worker*.txt -SimpleMatch "TERMINATE | SUCCESS"
```

정상이라면 Worker1~Worker4에 대해 총 네 줄이 출력된다. 명령어 화면이 불편하면
다음 명령으로 로그 폴더를 Windows 파일 탐색기에서 열 수 있다.

```powershell
explorer .\manual-test-logs
```

각 `WorkerN.txt`를 메모장으로 열고 맨 아래에 다음 두 줄이 있는지만 확인해도 된다.

```text
STAT | INFO | 처리량=...
TERMINATE | SUCCESS | workerN가 정상 종료했습니다.
```

다음 네 파일이 모두 있어야 한다.

```text
manual-test-logs\Worker1.txt
manual-test-logs\Worker2.txt
manual-test-logs\Worker3.txt
manual-test-logs\Worker4.txt
```

AWS Master 로그는 다음 명령으로 확인한다.

```bash
ls -lh /home/ubuntu/kvstore-manual-test-logs/Master.txt
tail -n 30 /home/ubuntu/kvstore-manual-test-logs/Master.txt
```

Master 로그 경로를 찾지 못하면 실행 명령에서 `--log-dir` 뒤에
`/home/ubuntu/kvstore-manual-test-logs`를 정확히 입력했는지 확인한다. `~` 또는 `\~`를
직접 입력하지 않는다.

<a id="step-9"></a>

## 9. 테스트가 끝난 뒤 EC2 중지하기

테스트가 끝나면 실행 시간을 줄이기 위해 EC2를 바로 중지한다.

1. AWS 콘솔의 **EC2 > 인스턴스 > 인스턴스** 화면으로 돌아간다.
2. `data-communications-master`를 선택한다.
3. **인스턴스 상태**를 누른다.
4. **인스턴스 중지**를 누른다.
5. 확인 창에서 다시 **중지**를 누른다.
6. 인스턴스 상태가 `중지됨`으로 바뀌었는지 확인한다.

인스턴스를 종료하면 서버와 로그를 복구하기 어려우므로 제출 전에는 **종료**가 아닌
**중지**를 선택한다.

<a id="step-10"></a>

## 10. 자주 발생하는 문제

### Master 실행 시 `Address already in use`가 나오는 경우

등록 확인용 서버가 5000번 포트를 사용 중일 가능성이 크다.

```bash
sudo systemctl disable --now kvstore-master.service
sudo ss -ltnp 'sport = :5000'
```

두 번째 명령에서 5000번을 사용하는 프로세스가 더 이상 보이지 않으면 Master를
다시 실행한다.

### Worker에서 `Connection refused` 또는 timeout이 나오는 경우

다음 항목을 순서대로 확인한다.

1. EC2가 `실행 중`인가
2. EC2 상태 검사가 통과했는가
3. Worker 명령의 `<AWS-IP>`를 현재 퍼블릭 IPv4 주소로 바꿨는가
4. AWS Master 터미널에 `0.0.0.0:5000에서 Worker 연결을 기다립니다`가 보이는가
5. EC2 보안 그룹에 TCP 5000 인바운드 규칙이 있는가

### 로컬에서 `No module named kvstore`가 나오는 경우

저장소 최상위 폴더에서 설치 명령을 다시 실행한다.

```powershell
.\.venv\Scripts\python.exe -m pip install -e .
```

### P2P 포트가 이미 사용 중인 경우

이전에 실행한 Worker가 남아 있지 않은지 확인하고 해당 PowerShell 창을 종료한 뒤
다시 실행한다. Worker 실행 명령을 동시에 두 번 실행하지 않는다.

<a id="step-11"></a>

## 11. 시연할 때 보여줄 순서

5분 이내 영상에서는 다음 순서로 핵심 화면만 보여준다.

1. AWS EC2가 서울 리전에서 실행 중인 화면
2. AWS Master의 5,000개 작업 생성과 대기 화면
3. 로컬 Worker 4개의 Master 연결 로그
4. Queue 경고와 작업 성공·실패 로그
5. 실패 작업의 다른 Worker 재할당 로그
6. Worker 간 P2P 전송과 ACK 로그
7. Master의 `5000 / 5000` 통계와 정상 종료
8. 생성된 `Master.txt`, `Worker1.txt`~`Worker4.txt`

영상에는 시연 과정을 설명하는 목소리가 반드시 포함되어야 한다.
