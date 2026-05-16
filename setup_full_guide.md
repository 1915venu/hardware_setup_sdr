# RPi + Quectel 5G APE Attack Testbed — Full Setup Guide

**Goal:** Reproduce the APE attack experiment: real 5G NR SA link from Raspberry Pi → gNB → Open5GS core, with PacketRusher flooding the AMF from a separate machine.

---

## Hardware Required

| Device | Role | Specs |
|--------|------|-------|
| **.4 PC** | gNB + 5G Core + Receiver | Ubuntu 24.04, ≥8 cores, USRP B210 via USB3 |
| **.2 PC** | Attacker + RPi gateway | Ubuntu 24.04, ≥8 cores, USB-LAN dongle |
| **Raspberry Pi 5** | UE (sender) | RPi 5 8GB, Sixfab HAT, Quectel RM502Q-AE modem, 2× 5G antennas |
| **USRP B210** | SDR radio (gNB antenna) | 2× antennas, USB3 to .4 PC |
| **Network switch / cables** | LAN | All machines on 192.168.10.0/24 |
| **USB-LAN dongle** | RPi ↔ .2 PC direct link | Plugged into .2 PC, cable to RPi eth0 |

---

## IP Address Plan

| Machine | Interface | IP |
|---------|-----------|-----|
| .4 PC | eno1 (main LAN) | 192.168.10.4 |
| .2 PC | eno1 (main LAN) | 192.168.10.2 |
| .2 PC | enxd03745c334fd (USB-LAN) | 192.168.10.5 |
| RPi | eth0 (cable to .2 PC USB-LAN) | 192.168.10.6 |
| RPi | wwan0 (5G modem) | 10.45.0.x/y (assigned by UPF per session) |
| Open5GS AMF | Docker (inside open5gs_5gc) | 10.53.1.2:38412 |
| Open5GS UPF | Docker bridge gateway | 10.53.1.1 |

---

## Part 1 — .4 PC Setup (gNB + 5G Core + Receiver)

### 1.1 Install Open5GS via Docker

```bash
# Pull and run Open5GS all-in-one container
docker pull gradiant/open5gs:2.7.1
docker run -d --name open5gs_5gc \
  --cap-add=NET_ADMIN \
  -p 38412:38412/sctp \
  -p 2152:2152/udp \
  gradiant/open5gs:2.7.1

# Verify running
docker ps | grep open5gs
```

### 1.2 Register the SIM in Open5GS

Access the Open5GS WebUI (default: http://192.168.10.4:3000) and add a subscriber:

| Field | Value |
|-------|-------|
| IMSI | `001010123456780` |
| K | `00112233445566778899aabbccddeeff` |
| OPC | `63BFA50EE6523365FF14C1F45F88737D` |
| APN | `internet` |
| SUCI scheme | Profile A (curve25519) |

### 1.3 Build or Install the gNB (OCUDU)

```bash
# Clone and build (adjust branch as needed)
git clone https://github.com/srsran/srsRAN_Project ~/ocudu
cd ~/ocudu && mkdir build && cd build
cmake .. -DCMAKE_BUILD_TYPE=Release
make -j$(nproc) gnb
```

### 1.4 Configure the gNB

Edit `~/ocudu/configs/gnb_rf_b210_fdd_srsUE.yml`:

```yaml
amf:
  addr: 10.53.1.2        # Open5GS AMF inside Docker
  bind_addr: 192.168.10.4

ru_sdr:
  device_driver: uhd
  device_args: type=b200
  srate: 11.52e6
  tx_gain: 75
  rx_gain: 75

cell_cfg:
  dl_arfcn: 368500        # Band 3, 1842.5 MHz
  band: 3
  channel_bandwidth_MHz: 10
  common_scs: 15
  plmn: "00101"
  tac: 7

slicing:
  - sst: 1
```

### 1.5 Expose AMF SCTP Port to LAN (for PacketRusher on .2 PC)

```bash
# On .4 PC — DNAT port 38412 from host into Docker
sudo iptables -t nat -A PREROUTING -i eno1 -p sctp --dport 38412 \
  -j DNAT --to-destination 10.53.1.2:38412
sudo iptables -A FORWARD -p sctp -d 10.53.1.2 --dport 38412 -j ACCEPT
sudo iptables -A FORWARD -p sctp -s 10.53.1.2 --sport 38412 -j ACCEPT

# Verify: from .2 PC you should be able to reach 10.53.1.2 via .4 PC
```

### 1.6 Configure chrony as NTP Server (for RPi clock sync)

```bash
# On .4 PC
sudo bash -c "cat >> /etc/chrony/chrony.conf << 'EOF'
local stratum 10
allow 192.168.10.0/24
EOF"
sudo systemctl restart chrony
```

### 1.7 Install receiver.py

```bash
mkdir -p ~/urllc_bench/results
# Copy receiver.py to ~/urllc_bench/receiver.py
# Copy launch_receiver.sh to ~/urllc_bench/scripts/launch_receiver.sh
```

`receiver.py` key parameters:
```python
HOST = '0.0.0.0'
PORT = 5005
DEADLINE_MS = 50.0        # change to 30.0 for tighter threshold
CSV_DIR = '~/urllc_bench/results/'
```

### 1.8 Apply Tier 1 Tuning (run after every boot)

```bash
sudo cpupower frequency-set -g performance
sudo cpupower idle-set -D 0
sudo sysctl -w vm.swappiness=0
sudo sysctl -w net.core.rmem_max=26214400
sudo sysctl -w net.core.wmem_max=26214400
sudo sysctl -w net.core.netdev_max_backlog=5000
```

---

## Part 2 — RPi Setup (UE / Sender)

### 2.1 Physical Assembly

1. Attach Quectel RM502Q-AE to Sixfab HAT
2. Connect 2× 5G NR antennas to modem (main + diversity ports)
3. Plug HAT into RPi 5 GPIO header (USB3 connection to modem)
4. Connect LAN cable: RPi eth0 → .2 PC USB-LAN dongle
5. Place RPi physically close to USRP B210 antennas (same room, 1–3 metres)

### 2.2 Install ModemManager

```bash
# On RPi
sudo apt update
sudo apt install -y modemmanager mmcli
sudo systemctl enable --now ModemManager
```

### 2.3 Switch Modem to MBIM Mode (one-time, survives reboot)

```bash
# On RPi — check modem is detected
mmcli -L
# Expected: /org/freedesktop/ModemManager1/Modem/0  [Quectel] RM502Q-AE

# Switch to MBIM
sudo mmcli -m 0 --command='AT+QCFG="usbnet",2'
sudo mmcli -m 0 --command='AT+CFUN=1,1'    # reboots modem
sleep 10

# Verify
sudo mmcli -m 0 --command='AT+QCFG="usbnet"'
# Expected: +QCFG: "usbnet",2
```

> **Why MBIM:** ECM mode answers ICMP locally (traffic never enters 5G bearer). QMI mode brings up wwan0 but kernel driver doesn't route into GTP-U data path. Only MBIM properly routes UDP through the 5G radio.

### 2.4 Configure NTP (point to .4 PC)

```bash
# On RPi — routing to .4 PC must be set up first (Part 3)
sudo bash -c "cat > /etc/systemd/timesyncd.conf << 'EOF'
[Time]
NTP=192.168.10.4
FallbackNTP=192.168.10.4
EOF"
sudo systemctl restart systemd-timesyncd
sleep 15
timedatectl show | grep NTPSynchronized
# Expected: NTPSynchronized=yes
```

### 2.5 Deploy sender.py

```bash
# From .2 PC — copy sender to RPi
scp ~/Desktop/sender_rpi.py priyansh@192.168.10.6:~/sender.py
```

`sender.py` sends 100 UDP packets/second to `10.53.1.1:5005`. Each packet carries: sequence number, send timestamp, robot command (drive/stop cycle). Total 20 bytes per packet.

---

## Part 3 — .2 PC Networking (Routing + Forwarding)

The .2 PC acts as a router between RPi and .4 PC, AND as the attack machine.

### 3.1 Fix Duplicate Subnet Route

When USB-LAN dongle comes up, Linux adds a conflicting route. Fix:

```bash
# On .2 PC — run after every boot
sudo ip route del 192.168.10.0/24 dev enxd03745c334fd 2>/dev/null
sudo ip route add 192.168.10.6 dev enxd03745c334fd   # host-only route
sudo ip route replace 10.53.1.0/24 via 192.168.10.4 dev eno1
```

### 3.2 Enable IP Forwarding + NAT for RPi

```bash
# On .2 PC
echo 1 | sudo tee /proc/sys/net/ipv4/ip_forward

sudo iptables -t nat -A POSTROUTING -s 192.168.10.6 -o eno1 -j MASQUERADE
sudo iptables -I FORWARD 1 -i enxd03745c334fd -o eno1 -j ACCEPT
sudo iptables -I FORWARD 1 -i eno1 -o enxd03745c334fd \
  -m state --state RELATED,ESTABLISHED -j ACCEPT
```

> **Note:** If Cilium/k8s is installed, the FORWARD chain policy is DROP by default. The explicit ACCEPT rules above are mandatory — enabling ip_forward alone is not enough.

### 3.3 Add Routes on RPi and .4 PC

```bash
# On RPi — route to .4 PC via .2 PC gateway
sudo ip route add 192.168.10.4 via 192.168.10.5 dev eth0
sudo ip route add 192.168.10.2 via 192.168.10.5 dev eth0

# On .4 PC — return route for RPi
echo space | sudo -S ip route add 192.168.10.6 via 192.168.10.2 dev enp2s0
```

### 3.4 Verify Connectivity

```bash
ping -c 1 192.168.10.4   # .2 → .4 PC: OK
ping -c 1 192.168.10.6   # .2 → RPi: OK
sshpass -p 'space' ssh priyansh@192.168.10.6 'ping -c 1 192.168.10.4'  # RPi → .4 PC: OK
```

---

## Part 4 — PacketRusher Setup (Attack Tool on .2 PC)

### 4.1 Build PacketRusher

```bash
# On .2 PC
git clone https://github.com/HewlettPackard/PacketRusher ~/PacketRusher
cd ~/PacketRusher
go build -o packetrusher .
```

### 4.2 Configure Base Config

Edit `~/PacketRusher/config/config.yml`:

```yaml
gnodeb:
  controlif:
    ip: "192.168.10.2"       # source IP for SCTP to AMF
    port: 9487
  dataif:
    ip: "192.168.10.2"
    port: 2152
  plmnlist:
    mcc: "001"
    mnc: "01"
    tac: "000007"
    gnbid: "000099"
  amfif:
    - ip: "192.168.10.4"     # .4 PC host — iptables DNATs to AMF container
      port: 38412

ue:
  msin: "0123456780"
  key: "00112233445566778899aabbccddeeff"
  opc: "63BFA50EE6523365FF14C1F45F88737D"
  hplmn:
    mcc: "001"
    mnc: "01"
  snssai:
    sst: 1
  dnn: "internet"
```

### 4.3 Create IP Aliases and Per-Instance Configs (one-time)

```bash
bash ~/urllc_bench/scripts/setup_attack_8.sh
# Adds aliases 192.168.10.22–.29 on eno1
# Creates config_22.yml through config_29.yml
```

---

## Part 5 — Deploy All Scripts

Copy the `urllc_bench/scripts/` directory to all machines:

```bash
# On .2 PC (already there)
ls ~/urllc_bench/scripts/

# Copy scripts to .4 PC
sshpass -p 'space' scp -r ~/urllc_bench/scripts/ priyansh@192.168.10.4:~/urllc_bench/scripts/
sshpass -p 'space' ssh priyansh@192.168.10.4 'mkdir -p ~/urllc_bench/results'
```

---

## Part 6 — Running the Experiment

### 6.1 Every Time After Reboot — Preflight

```bash
# On .2 PC — fixes all routing and wwan0 IP sync
bash ~/urllc_bench/scripts/preflight.sh
# Expect: .2→.4 PC: OK, .2→RPi: OK, RPi→.4 PC: OK
```

### 6.2 Start the Radio

```bash
# Tier 1 tuning on .4 PC
sshpass -p 'space' ssh priyansh@192.168.10.4 'bash ~/urllc_bench/scripts/tier1_dot4.sh'

# Start gNB
sshpass -p 'space' ssh priyansh@192.168.10.4 'bash ~/urllc_bench/scripts/launch_gnb.sh'
sleep 15

# Confirm gNB connected to AMF
sshpass -p 'space' ssh priyansh@192.168.10.4 'grep "Connected to AMF" /tmp/gnb.log | tail -1'
# Expected: Connected to AMF. Supported PLMNs: 00101
```

### 6.3 Connect the 5G Modem

```bash
# Connect modem (index may be 0 or 1 — check with: mmcli -L)
sshpass -p 'space' ssh priyansh@192.168.10.6 'sudo mmcli -m 1 --simple-connect="apn=internet"'
# Expected: successfully connected the modem

# Verify 5G connection in gNB log (look for PUCCH with SINR >15dB)
sshpass -p 'space' ssh priyansh@192.168.10.4 'tail -5 /tmp/gnb.log'
# Expected: PUCCH: rnti=0xXXXX ... sinr=XX.XdB

# Sync wwan0 IP with bearer (critical — do after every connect)
bash ~/urllc_bench/scripts/preflight.sh
```

### 6.4 Run Clean Baseline (120s)

```bash
bash ~/urllc_bench/scripts/run_baseline.sh 120
# Expect: Mean ~15ms, p99 ~25ms, Max ~27ms, Violations 0%
# If violations > 0% — STOP. Debug before running attack.
```

### 6.5 Run Standard Attack (4-instance, 120s)

```bash
bash ~/urllc_bench/scripts/run_attack.sh 120
# Launches 4 PacketRusher instances
# Runs sender on RPi for 120s
# Prints stats + saves CSV to ~/Desktop/attack_scaled_<timestamp>.csv
```

### 6.6 Run Pressure Attack (8-instance + Docker CPU cap + forced reconnect)

#### Why This Scenario

Earlier experiments showed that flooding the AMF with fake registrations has zero effect on packets already in flight — the 5G data path (wwan0 → gNB → GTP-U → UPF → receiver) bypasses the AMF entirely. The attack only matters when the real UE needs to re-register. This scenario is designed to force exactly that: the modem is disconnected and forced to re-register while the AMF is under maximum load, exposing the control-plane bottleneck.

Two pressure mechanisms are combined:
- **Docker CPU cap** — the entire `open5gs_5gc` container is hard-limited to 1.5 CPU cores, so AMF, UDM, and UPF must share a constrained budget
- **8-instance flood** — 8 PacketRusher instances from 8 different IP aliases each create an independent SCTP association to the AMF, generating 1,600 fake registrations/second, saturating the AMF's SUCI deconcealment queue

Once the attack is fully running, the real modem is force-disconnected and immediately reconnected. Its registration request now sits in a queue behind hundreds of fake ones.

#### Pre-requisite — One-time Setup

```bash
# 1. Add 8 IP aliases on eno1 (.22–.29) and create per-instance PacketRusher configs
bash ~/urllc_bench/scripts/setup_attack_8.sh
# Expected output: 8 aliases added, config_22.yml through config_29.yml created

# 2. Verify aliases exist
ip addr show eno1 | grep "192.168.10.2[2-9]"
# Expected: 8 lines, one per alias

# 3. Verify AMF is reachable from .2 PC (iptables DNAT must be active on .4 PC)
ping -c 1 10.53.1.2
# Expected: 1 received
```

#### Run the Attack

```bash
bash ~/urllc_bench/scripts/run_pressure_attack.sh 30 90 1.5
#                                                  ^   ^   ^
#                                   baseline secs  |   |   Docker CPU cap (cores)
#                                   attack secs ───┘   |
#                                                       └── attack phase secs
```

#### Step-by-Step Flow

| Step | What Happens | Duration |
|------|-------------|----------|
| Phase 1 | Sender runs normally — no attack, no CPU cap. Establishes baseline OWD in the same CSV | 30s |
| Setup | Docker container capped to 1.5 CPUs via `docker update --cpus=1.5` | instant |
| Launch | 8 PacketRusher instances start, each pinned to a separate CPU core (0–7), each connecting to AMF via its own SCTP association | — |
| Wait | Script waits 15s for all SCTP associations to establish, then 20s for AMF to fully saturate. AMF CPU is printed before proceeding | 35s |
| Phase 3 | Modem disconnected (`mmcli --simple-disconnect`), immediately reconnected (`mmcli --simple-connect`). Timer runs from disconnect to new bearer confirmed. New bearer IP synced to wwan0 | measured |
| Phase 4 | Sender runs again for 90s under active attack. OWD measured post-reconnect | 90s |
| Cleanup | Attackers killed, Docker CPU limit removed (`docker update --cpus=0`), full stats printed, CSV saved to Desktop | — |

#### What to Watch During the Run

```bash
# In a separate terminal — monitor AMF CPU live
watch -n 2 "sshpass -p 'space' ssh priyansh@192.168.10.4 \
  'docker stats --no-stream open5gs_5gc --format \"CPU: {{.CPUPerc}}\"'"

# Check SCTP associations established (after launch)
grep "SCTP connection established" /tmp/attack_logs_*/attack_*.log | wc -l
# Expected: 8

# Watch gNB for UE disconnect/reconnect events
sshpass -p 'space' ssh priyansh@192.168.10.4 \
  'tail -f /tmp/gnb.log | grep -E "Release|attach|rnti"'
```

#### Expected Results

```
Phase 1 (baseline):
  Mean OWD: ~15ms    Max: ~27ms    Violations: 0%

AMF CPU under 8-instance attack: ~40–60% of 1.5-core cap

Reconnect time (no attack):    762 ms
Reconnect time (under attack): ~3,200 ms    ← 4.2× slower
Packets lost during blackout:  ~325         ← 3.25s × 100Hz, zero commands reach robot

Phase 4 (90s post-reconnect under attack):
  Mean OWD: ~15ms    Max: ~34ms    Violations: 0%
  (data plane resilient once connection restored)
```

#### Interpreting the Result

The 3,251ms reconnect is not all caused by the attack — a normal reconnect takes 762ms regardless. The attack adds ~2,490ms on top. The significance is not the absolute number but the **threshold crossing**: 762ms is within acceptable limits for robot control (robot barely drifts); 3,251ms is not — the robot receives zero commands for over 3 seconds. Any non-trivial motion results in collision or runaway. This is the paper's core claim demonstrated on real commercial hardware.

---

## Part 7 — Critical Bugs to Remember

| Bug | Symptom | Fix |
|-----|---------|-----|
| **MBIM bearer IP mismatch** | Sender TX counter goes up, receiver CSV empty, gNB shows no PUSCH | Run `preflight.sh` after every modem reconnect — always reads IP/GW from `mmcli -b 0` |
| **Gateway changes per bearer** | `ENETUNREACH` on sender | Never hardcode `10.45.0.13` — always read GW from `mmcli -b 0` |
| **gNB inactivity timer** | Bearer context released after 60s, CSV empty | Start sender BEFORE launching attack to keep DRB alive |
| **Clock skew** | All packets show 1200ms+ OWD | Configure timesyncd on RPi pointing to .4 PC chrony |
| **Duplicate subnet route on .2 PC** | .4 PC unreachable when USB-LAN up | `ip route del 192.168.10.0/24 dev enxd...` then `ip route add 192.168.10.6 dev enxd...` |
| **iptables FORWARD DROP** | RPi can't reach .4 PC despite ip_forward=1 | Must add explicit FORWARD ACCEPT rules — not just enable forwarding |
| **Wrong modem index** | `mmcli -m 0` fails | Check with `mmcli -L` — modem index can be 0 or 1 |
| **ICMP blocked through UPF** | ping to 10.53.1.1 fails despite working data path | Test with UDP (sender.py) not ping — ICMP is not routed through GTP-U |

---

## Part 8 — Quick Reference

```bash
# Full reset (keep gNB running)
bash ~/urllc_bench/scripts/killswitch.sh

# If Open5GS crashed
sshpass -p 'space' ssh priyansh@192.168.10.4 'docker restart open5gs_5gc'

# If gNB crashed
sshpass -p 'space' ssh priyansh@192.168.10.4 \
  'echo space | sudo -S pkill -9 -f "apps/gnb/gnb"; bash ~/urllc_bench/scripts/launch_gnb.sh'

# If modem disconnected
sshpass -p 'space' ssh priyansh@192.168.10.6 \
  'sudo mmcli -m 1 --simple-disconnect; sleep 2; sudo mmcli -m 1 --simple-connect="apn=internet"'
# Then: bash ~/urllc_bench/scripts/preflight.sh

# Analyze any CSV
bash ~/urllc_bench/scripts/analyze_csv.sh                    # latest from .4 PC
bash ~/urllc_bench/scripts/analyze_csv.sh baseline_XYZ.csv  # specific file
```
