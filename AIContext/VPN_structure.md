# 개인 WireGuard VPN 구축 문서

- 작성일: 2026-07-15
- 목적: 한국에서 접속 제한된 해외 사이트를 아이폰에서 우회 접속
- 원칙: 기존 자산관리 프로젝트(asset-cloud)와 완전히 분리, 서로 영향 없음

---

## 1. 인프라 개요

| 항목 | 값 |
|---|---|
| 클라우드 | Oracle Cloud Infrastructure (OCI) Always Free 티어 |
| 인스턴스 | `instance-20260523-0649` |
| 리전 | `ap-tokyo-1` (도쿄) — 한국 내 차단 우회 목적상 의도적으로 해외 리전 사용 |
| OS | Ubuntu 22.04.5 LTS (커널 `6.8.0-1049-oracle`, aarch64/Ampere A1) |
| VCN | `vcn-20260520-new` |
| Subnet | `subnet-20260520-new` |
| 외부 인터페이스 | `enp0s6` (사설 IP `10.0.0.107/24`) |
| 공인 IP | `161.33.151.220` |
| VPN 소프트웨어 | WireGuard (in-kernel, Ubuntu 22.04 apt 패키지) |
| VPN 포트 | UDP `51820` |
| VPN 내부 대역 | `10.8.0.0/24` (기존 VCN 대역 `10.0.0.0/24`와 겹치지 않도록 별도 지정) |
| 서버(wg0) 주소 | `10.8.0.1/24` |
| 아이폰(첫 번째 peer) | `10.8.0.2/32` |

---

## 2. 기존 프로젝트와의 격리 설계

- PostgreSQL(5432), Redis(6379)는 **loopback(127.0.0.1)에만 바인딩**되어 있어, WireGuard를 포함한 어떤 외부 네트워크 경로로도 원천적으로 접근 불가능. (별도 조치 없이 이미 격리된 상태)
- nginx(80/443), uvicorn(8080)은 VPN 설치 전/후 `curl -I` 응답 동일함을 실측 확인함 → 영향 없음.
- iptables INPUT 체인에는 **기존 규칙을 건드리지 않고 UDP 51820 ACCEPT 규칙 1줄만 추가**(맨 앞 삽입, REJECT 규칙보다 우선순위 높게).
- `net.ipv4.ip_forward=1`은 커널 전역 설정이라 유일하게 전역적으로 바뀐 부분. 지금까지 자산관리 프로젝트에 영향 없음을 확인했으나, 완전히 격리되진 않은 유일한 지점이므로 향후 문제 생기면 이 부분부터 의심할 것.

---

## 3. 파일/설정 위치

```
/etc/wireguard/
├── wg0.conf              # 서버 설정 (600, root:root)
├── server_private.key    # 서버 프라이빗 키 (600, root:root)
├── server_public.key     # 서버 퍼블릭 키
├── iphone_private.key    # 아이폰(peer #1) 프라이빗 키
├── iphone_public.key     # 아이폰(peer #1) 퍼블릭 키
└── iphone.conf           # 아이폰에 넣어준 클라이언트 설정 (QR코드 생성 원본)
```

### wg0.conf 구조 (서버)

```ini
[Interface]
Address = 10.8.0.1/24
ListenPort = 51820
PrivateKey = <server_private.key 내용>
PostUp = iptables -I FORWARD 1 -i wg0 -j ACCEPT; iptables -I FORWARD 1 -o wg0 -j ACCEPT; iptables -t nat -A POSTROUTING -o enp0s6 -j MASQUERADE
PostDown = iptables -D FORWARD -i wg0 -j ACCEPT; iptables -D FORWARD -o wg0 -j ACCEPT; iptables -t nat -D POSTROUTING -o enp0s6 -j MASQUERADE

[Peer]                      # 아이폰
PublicKey = <iphone_public.key 내용>
AllowedIPs = 10.8.0.2/32
```

### iphone.conf 구조 (클라이언트, 아이폰에 QR로 전달)

```ini
[Interface]
PrivateKey = <iphone_private.key 내용>
Address = 10.8.0.2/32
DNS = 1.1.1.1

[Peer]
PublicKey = <server_public.key 내용>
Endpoint = 161.33.151.220:51820
AllowedIPs = 0.0.0.0/0, ::/0     # 풀 터널: 아이폰의 모든 트래픽이 서버를 경유
PersistentKeepalive = 25
```

> **주의**: `AllowedIPs = 0.0.0.0/0, ::/0`은 풀 터널(전체 트래픽 우회) 설정. 특정 앱/사이트만 우회하고 싶다면 이 값을 좁혀야 함(스플릿 터널은 별도 설계 필요).

---

## 4. 방화벽 설정 (2곳 모두 필요)

VPN이 동작하려면 **①VM 내부 iptables**와 **②OCI 클라우드 레벨 Security List** 양쪽 다 열려 있어야 함. 하나라도 막히면 핸드셰이크 자체가 안 됨.

### ① VM 내부 (iptables)

```bash
# 확인
sudo iptables -L INPUT -n --line-numbers

# 규칙 (이미 적용됨) - REJECT 규칙보다 앞에 있어야 함
sudo iptables -I INPUT 1 -p udp --dport 51820 -j ACCEPT

# 영구 저장 (기존 80/443/8080/22 규칙과 동일한 방식)
sudo netfilter-persistent save
```

### ② OCI 콘솔 (Security List) — 콘솔에서만 가능, CLI/터미널로 확인 불가

경로: OCI 콘솔 → ☰ → Networking → Virtual Cloud Networks → `vcn-20260520-new` → Resources → Security Lists → Default Security List → Ingress Rules → Add Ingress Rules

설정값:
- Stateless: 체크 안 함
- Source Type: CIDR
- Source CIDR: `0.0.0.0/0`
- IP Protocol: UDP
- Destination Port Range: `51820`

---

## 5. 아이폰 설치 방법 (QR 코드)

```bash
sudo bash -c '
IPHONE_PRIV=$(cat /etc/wireguard/iphone_private.key)
SERVER_PUB=$(cat /etc/wireguard/server_public.key)

cat > /etc/wireguard/iphone.conf <<EOF
[Interface]
PrivateKey = ${IPHONE_PRIV}
Address = 10.8.0.2/32
DNS = 1.1.1.1

[Peer]
PublicKey = ${SERVER_PUB}
Endpoint = 161.33.151.220:51820
AllowedIPs = 0.0.0.0/0, ::/0
PersistentKeepalive = 25
EOF

chmod 600 /etc/wireguard/iphone.conf
qrencode -t ansiutf8 < /etc/wireguard/iphone.conf
'
```

아이폰 측:
1. App Store에서 **WireGuard** 공식 앱 설치
2. 앱 실행 → `+` 버튼 → **"QR 코드 또는 이미지에서 생성"**
3. 터미널에 뜬 QR코드 스캔
4. 터널 토글 ON
5. (선택) 터널 편집 → **On-Demand** 활성화 → Wi-Fi/Cellular 선택 시 iOS가 자동 재연결 유지

### 새 기기(2번째 peer) 추가 시

```bash
# 1. 새 키 쌍 생성 (예: 맥북용)
sudo bash -c '
wg genkey | tee /etc/wireguard/macbook_private.key | wg pubkey > /etc/wireguard/macbook_public.key
chmod 600 /etc/wireguard/macbook_*.key
'

# 2. wg0.conf에 Peer 블록 추가 (AllowedIPs는 10.8.0.3/32처럼 안 겹치게)
# 3. wg-quick down wg0 && wg-quick up wg0 로 재적용 (또는 wg syncconf 사용)
# 4. 새 클라이언트 conf 만들어서 QR 생성 (위 4번 방식과 동일, Endpoint/서버 공개키는 동일)
```

---

## 6. 상태 확인 / 트러블슈팅 명령어 모음

```bash
# 터널 상태, 핸드셰이크, 트래픽량 확인
sudo wg show

# 핸드셰이크가 안 잡힐 때: 패킷이 서버까지 도달하는지 실시간 확인
sudo tcpdump -i enp0s6 udp port 51820 -n -c 20
# (실행한 상태에서 아이폰 WireGuard 토글 껐다 켜서 테스트)

# NAT MASQUERADE 규칙 확인 (패킷 카운트 쌓이는지)
sudo iptables -t nat -L POSTROUTING -n -v

# FORWARD 체인 확인
sudo iptables -L FORWARD -n -v --line-numbers

# 서비스 상태
sudo systemctl status wg-quick@wg0

# 부팅시 자동시작 여부
systemctl is-enabled wg-quick@wg0
```

### 겪었던 이슈 및 원인 (재발 방지용 기록)

| 증상 | 원인 | 해결 |
|---|---|---|
| `cd /etc/wireguard` Permission denied | 디렉터리가 `700 root:root`라 일반 계정 진입 불가 | `sudo bash -c '...'`로 root 셸 안에서 절대경로 사용 |
| 키 파일이 엉뚱한 곳(`~/asset-cloud`)에 생성됨 | cd 실패 후에도 후속 명령이 계속 실행되어 현재 디렉터리 기준 상대경로로 저장됨 | 파일 위치 확인 후 `mv`로 `/etc/wireguard`에 이동, 권한 재설정 |
| `wg0.conf`의 `PrivateKey =`, `PublicKey =`가 빈 값 | 변수 할당 시점에 원본 키 파일 경로가 잘못되어 빈 문자열이 대입됨 | `sudo bash -c` 안에서 `cat`으로 직접 읽어 `sed`로 재주입 |
| `chmod 600 /etc/wireguard/*.key` 실패 (No such file) | `sudo`는 `chmod`만 root 권한으로 실행하고, `*.key` 와일드카드는 그 앞에서 일반 계정 셸이 먼저 펼치려다 실패 | `sudo bash -c 'chmod 600 /etc/wireguard/*.key'`로 와일드카드 확장까지 root 셸 안에서 처리 |
| 연결은 되는데 인터넷 안 됨 (`wg show`에 handshake 없음) | 단계적으로 확인: ①OCI Security List ②OS iptables ③tcpdump로 실제 패킷 도달 여부 | Security List에 UDP 51820 Ingress Rule 누락이 원인 → 추가 후 해결 |

---

## 7. 네트워크(트래픽) 사용량 측정 방식

### 목적
오라클 Always Free 티어의 **아웃바운드(egress) 월 10TB 한도** 초과 여부 확인.

### 왜 OCI 콘솔의 "Cost Analysis"로는 확인이 안 되는가
Cost Analysis는 **금액($) 기준** 화면. 무료 한도 안에서 쓰는 트래픽은 과금 자체가 안 되므로 계속 $0으로 표시됨. 즉 "0"은 "안 썼다"가 아니라 "과금된 적 없다"는 뜻.

### 현재 사용 중인 방법 (2026-07-15 기준, 임시)

부팅 이후 인터페이스 누적 바이트를 커널에서 직접 확인:

```bash
cat /proc/net/dev
# enp0s6 줄의 마지막에서 두번째 컬럼(bytes, 수신) / 송신(bytes) 확인
uptime -p   # 부팅 후 경과 시간 확인 (페이스 계산용)
```

측정 시점 스냅샷 (참고용, 계속 누적되며 변함):
- 부팅 후 53일 경과 시점 기준 TX(송신/아웃바운드) 누적 약 12.53GB
- 일평균 약 0.236GB/일 → 월 환산 약 7GB → 10TB 한도의 약 0.07%
- **한계**: 재부팅하면 리셋됨, 정확한 "이번 달" 단위 집계 아님

### 향후 개선 예정 (미구현)

1. `vnstat` 설치 → 일/주/월 단위로 지속 기록되는 트래픽 데이터베이스 구축
   ```bash
   sudo apt install -y vnstat
   sudo systemctl enable --now vnstat
   vnstat -i enp0s6 -m    # 월별 집계
   ```
2. **Telegram 알림 봇** (예정, 미구현):
   - BotFather로 봇 생성, chat_id 확보
   - vnstat 수치를 주기적으로 조회해 Telegram Bot API로 전송하는 스크립트 작성
   - cron으로 일간/주간 자동 발송
   - → 별도 세션에서 진행 예정

---

## 8. 보안 메모

- `/etc/wireguard/*.key` 전부 `600 root:root` 권한 유지 확인됨.
- 서버/클라이언트 프라이빗 키는 이 문서에는 값 자체를 적지 않음 — 실제 값은 VM의 `/etc/wireguard/` 안에만 존재. 필요 시 서버에서 직접 `sudo cat`으로 조회.
- 키가 유출됐다고 의심되면: 해당 peer 키 재생성 → `wg0.conf`의 `[Peer]` PublicKey 교체 → `wg syncconf` 또는 `wg-quick down/up`으로 재적용 → 클라이언트 conf도 재발급.