# RPi + Quectel RM502Q-AE — APE Attack Experiment Report

**Project:** NDSS Paper — APE Attack Against 5G AKA Protocol  
**Institution:** IIT Delhi  
**Conducted:** 2026-05-16 (Sessions 4 and 5)  
**UE:** Raspberry Pi 5 + Quectel RM502Q-AE (commercial 5G NR SA modem, MBIM mode)  
**Status:** Clean baseline confirmed. Scaled attack (8-instance, 51% AMF CPU) demonstrated. Established data paths fully resilient; reconnect time +10% at peak AMF load. Primary attack surface is the control plane — data plane bypasses AMF.

---

## Table of Contents

1. [Experiment Goal](#1-experiment-goal)
2. [Hardware Topology](#2-hardware-topology)
3. [Modem Configuration — MBIM Mode](#3-modem-configuration--mbim-mode)
4. [Multi-Machine Networking](#4-multi-machine-networking)
5. [Clock Synchronization](#5-clock-synchronization)
6. [MBIM Bearer IP Mismatch Fix](#6-mbim-bearer-ip-mismatch-fix)
7. [Baseline Results](#7-baseline-results)
8. [Attack Results](#8-attack-results)
9. [Key Insight: Control Plane vs. Data Plane](#9-key-insight-control-plane-vs-data-plane)
10. [Summary Table](#10-summary-table)
11. [Next Steps for Stronger Attack](#11-next-steps-for-stronger-attack)
12. [Scripts and File Manifest](#12-scripts-and-file-manifest)


---

## 1. Experiment Goal

Replace the software UE (srsUE + USRP B210 on .2 PC) with a **real commercial 5G modem** (Quectel RM502Q-AE on Raspberry Pi 5) to demonstrate the APE attack against actual hardware. 

**The metric:** One-Way Delay (OWD) of UDP packets sent at 100Hz from the RPi through the 5G radio to a receiver on the .4 PC. Deadline = 50ms. Each packet above this threshold is a "violation" — the robot command arrived too late.

---

## 2. Hardware Topology

### Machines

| Machine | Hostname | IP | Role |
|---------|----------|----|------|
| `.2 PC` | venu | `192.168.10.2` (eno1), `192.168.10.5` (USB-LAN) | Attack host + IP forwarding gateway for RPi |
| `.4 PC` | priyansh | `192.168.10.4` | gNB + Open5GS + receiver |
| RPi 5 | sixfab | `192.168.10.6` (eth0) | **UE — sender** |

### RPi Hardware

| Component | Details |
|-----------|---------|
| Board | Raspberry Pi 5 (8GB RAM) |
| Modem | Quectel RM502Q-AE (5G NR Sub-6 SA) |
| HAT | Sixfab 5G/LTE HAT (USB3 interface to modem) |
| Antennas | 2× 5G NR antennas on modem |
| LAN | eth0 → USB-LAN dongle on .2 PC (192.168.10.5) |
| 5G | wwan0 → MBIM bearer → 10.45.0.x/30 (assigned per session) |

### Network Diagram

```
RPi (192.168.10.6)
  │ eth0
  ▼
.2 PC USB-LAN (192.168.10.5)  ←── IP forwarding + MASQUERADE ──►  .4 PC (192.168.10.4)
                                                                      gNB + Open5GS
                                                                      receiver.py :5005

RPi wwan0 (10.45.0.x)
  │ 5G NR radio (air interface)
  ▼
.4 PC gNB (OCUDU)
  │ GTP-U (N3 interface)
  ▼
UPF (inside open5gs_5gc Docker)
  │ Docker bridge: 10.53.1.1
  ▼
receiver.py (UDP :5005) → /cmd_vel → TurtleBot3 in Gazebo
```

**Key networking fact:** RPi has **no direct Layer 2 path to .4 PC**. eth0 connects to .2 PC only. .2 PC acts as a forwarding router — without the routing fixes in §4, RPi cannot reach .4 PC even on the control plane (NTP, SSH).

### .4 PC Software

| Component | Details |
|-----------|---------|
| gNB | OCUDU (`~/ocudu/build/apps/gnb/gnb`) |
| 5G Core | Open5GS in Docker (`open5gs_5gc`) — AMF at `10.53.1.2:38412`, UPF on `ogstun` |
| Receiver | `receiver.py` — UDP → TwistStamped → /cmd_vel |
| ROS2 | Jazzy Jalisco |
| Gazebo | Harmonic + TurtleBot3 burger |

### PLMN / SIM Credentials

| Parameter | Value |
|-----------|-------|
| PLMN | MCC=001, MNC=01 |
| TAC | 7, Slice SST=1 |
| IMSI | `001010123456780` |
| K | `00112233445566778899aabbccddeeff` |
| OPC | `63BFA50EE6523365FF14C1F45F88737D` |
| SUCI scheme | Profile A (curve25519, scheme 1) |

---

## 3. Modem Configuration — MBIM Mode

Three USB data modes were tested. Only **MBIM** works for this testbed.

| Mode | AT command | Interface | Problem |
|------|-----------|-----------|---------|
| ECM | `AT+QCFG="usbnet",1` | `usb0` | Modem answers ICMP locally; UDP never enters 5G bearer (silent drop) |
| QMI RMNET | `AT+QCFG="usbnet",0` | `wwan0` | Interface comes up but kernel qmi_wwan driver doesn't route into GTP-U data path; PUSCH shows `tbs=0` (no uplink data) |
| **MBIM** | `AT+QCFG="usbnet",2` | `wwan0` | ModemManager manages bearer cleanly; IP assigned via mmcli; traffic flows through 5G radio via GTP-U |

**One-time MBIM setup (survives reboot):**
```bash
# On RPi
mmcli -m 0 --command='AT+QCFG="usbnet",2'   # switch to MBIM
mmcli -m 0 --command='AT+CFUN=1,1'           # reboot modem
sleep 10
mmcli -m 0 --command='AT+QCFG="usbnet"'
# Expected: +QCFG: "usbnet",2
```

**Connect to network each session:**
```bash
# On RPi — modem index may be 0 or 1; check with: mmcli -L
sudo mmcli -m 1 --simple-connect='apn=internet'
# Expected: successfully connected the modem
```

**Verify connection:**
```bash
sudo mmcli -b 0           # shows bearer: address, gateway, prefix
ip addr show wwan0        # should match bearer address
ip route show dev wwan0   # default route via bearer gateway
```

---

## 4. Multi-Machine Networking

### Problem 1 — Duplicate Subnet Route on .2 PC

When the USB-LAN dongle connects to RPi, the kernel adds a `192.168.10.0/24` route on `enxd03745c334fd` — this conflicts with the existing `192.168.10.0/24` on `eno1`. Result: traffic to .4 PC (192.168.10.4) is sent over the USB-LAN instead of eno1 → .4 PC unreachable.

**Fix:**
```bash
# On .2 PC
sudo ip route del 192.168.10.0/24 dev enxd03745c334fd 2>/dev/null
sudo ip route add 192.168.10.6 dev enxd03745c334fd   # host route only, not subnet
```

### Problem 2 — RPi Has No Route to .4 PC

RPi's eth0 is plugged into .2 PC's USB-LAN only. .4 PC is on the main LAN. RPi needs .4 PC for: NTP sync, receiver.py target address verification.

**Fix — IP forwarding + MASQUERADE on .2 PC:**
```bash
# On .2 PC — enable forwarding and NAT
echo 1 | sudo tee /proc/sys/net/ipv4/ip_forward
sudo iptables -t nat -A POSTROUTING -s 192.168.10.6 -o eno1 -j MASQUERADE
sudo iptables -I FORWARD 1 -i enxd03745c334fd -o eno1 -j ACCEPT
sudo iptables -I FORWARD 1 -i eno1 -o enxd03745c334fd \
  -m state --state RELATED,ESTABLISHED -j ACCEPT

# On RPi — add routes via .2 PC gateway
sudo ip route add 192.168.10.4 via 192.168.10.5 dev eth0
sudo ip route add 192.168.10.2 via 192.168.10.5 dev eth0

# On .4 PC — add return route for RPi
echo space | sudo -S ip route add 192.168.10.6 via 192.168.10.2 dev enp2s0
```

> **Note:** .2 PC has `iptables` FORWARD chain with DROP policy (Cilium/k8s installed). Must explicitly add ACCEPT rules; enabling ip_forward alone is insufficient.

### Problem 3 — GTP-U Route on RPi

After modem connects, RPi needs a route for `10.53.1.0/24` (Docker bridge subnet where receiver.py listens) via the 5G bearer gateway:

```bash
# On RPi — add route through 5G bearer (gateway varies per session — always read from mmcli)
BEARER_GW=$(sudo mmcli -b 0 | grep "gateway:" | awk '{print $3}')
sudo ip route replace 10.53.1.0/24 via "$BEARER_GW" dev wwan0
```

### Automated Fix — preflight.sh

All routing fixes are automated in `~/urllc_bench/scripts/preflight.sh`. Run after every reboot:

```bash
bash ~/urllc_bench/scripts/preflight.sh
# Expect: ".2 → .4 PC: OK", ".2 → RPi: OK", "RPi → .4 PC: OK" at the end
```

---

## 5. Clock Synchronization

OWD = `t_recv (.4 PC)` − `t_send (RPi)`. With clock skew Δ: measured OWD = true OWD + Δ.

**Initial state (no NTP on RPi):** clock offset ~1228ms → every packet flagged as violation even though radio was fine.

| State | Clock offset | Measured OWD |
|-------|-------------|--------------|
| Initial — no NTP | ~1228 ms | ~1228 ms (100% violations) |
| After manual `date -s` correction | ~50–100 ms | ~60–110 ms (some violations) |
| **After NTP sync** | **<5 ms** | **~15 ms (true OWD)** |

**Root cause:** RPi has no internet access. .4 PC runs chrony at stratum 10 (local reference). Without explicit configuration, RPi clock drifts freely.

**Fix — NTP via routed path:**
```bash
# On .4 PC — chrony already serves NTP to local subnet:
#   /etc/chrony/chrony.conf: local stratum 10 / allow 192.168.10.0/24

# On RPi — point timesyncd to .4 PC:
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

**Verify before every experiment:**
```bash
echo "RPi:   $(sshpass -p 'space' ssh priyansh@192.168.10.6 'date -u')"
echo ".4 PC: $(sshpass -p 'space' ssh priyansh@192.168.10.4 'date -u')"
# Must agree within 1 second
```

---

## 6. MBIM Bearer IP Mismatch Fix

**This is the most critical operational fix discovered during these experiments.**

### What Happens

After each `mmcli --simple-connect`, the UPF allocates a **new IP** from its pool (e.g., 10.45.0.14/30 for one session, 10.45.0.18/30 for the next). The kernel's `wwan0` interface **retains the old IP** from the previous session. The MBIM driver silently drops all outbound packets whose source IP doesn't match the bearer IP.

**Symptom:** Sender reports successful `sendto()` (TX counters increase), but gNB log shows no PUSCH data (`tbs=0`), and receiver CSV is empty.

**Gateway also varies per bearer:** Each /30 subnet has a different gateway. If the route still points to the previous gateway (e.g., 10.45.0.13 from the old /30), packets get `ENETUNREACH` — the gateway is not in the new subnet.

### Fix

```bash
# On RPi — sync wwan0 with current MBIM bearer (run after every modem reconnect)
BEARER_IP=$(sudo mmcli -b 0 | grep "address:" | awk '{print $3}')
BEARER_PFX=$(sudo mmcli -b 0 | grep "prefix:" | awk '{print $3}')
BEARER_GW=$(sudo mmcli -b 0 | grep "gateway:" | awk '{print $3}')

WWAN_IP=$(ip -4 -o addr show wwan0 | awk '{print $4}' | cut -d/ -f1)
if [ "$BEARER_IP" != "$WWAN_IP" ]; then
  sudo ip addr flush dev wwan0
  sudo ip addr add "${BEARER_IP}/${BEARER_PFX}" dev wwan0
fi
sudo ip route replace 10.53.1.0/24 via "$BEARER_GW" dev wwan0
```

This fix is embedded in `preflight.sh` and `run_baseline.sh`.

---

## 7. Baseline Results

### Session 4 Baseline — First RPi Run (120s)

**Date:** 2026-05-16  
**File:** `~/Desktop/rpi_quectel_baseline_1778880953.csv` (.2 PC)  
**Conditions:** Quectel MBIM bearer synced, NTP synced, Tier 1 tuning on .4 PC, no attack

| Metric | Value |
|--------|-------|
| N packets | **11,929** |
| Duration | 120 s |
| Mean OWD | **14.86 ms** |
| Std (jitter) | 5.62 ms |
| p50 | 14.83 ms |
| p90 | 22.70 ms |
| p95 | 23.68 ms |
| p99 | **24.47 ms** |
| Max | **25.77 ms** |
| **Violations (≥50ms)** | **0 / 11,929 = 0.00%** |

**Signal quality (gNB log):** CSI SINR 27.5–29 dB. Both antennas contributing to MIMO diversity. No RF underflows.

### Session 5 Baseline — Repeat Confirmation (120s)

**Date:** 2026-05-16  
**File:** `.4 PC: ~/urllc_bench/results/baseline_1778915512.csv`  
**Conditions:** Same setup, after MBIM bearer IP mismatch fix applied to scripts

| Metric | Value |
|--------|-------|
| N packets | **11,929** |
| Duration | 120 s |
| Mean OWD | **15.32 ms** |
| Std (jitter) | 5.60 ms |
| p50 | 15.26 ms |
| p90 | 23.11 ms |
| p99 | **24.91 ms** |
| Max | **26.41 ms** |
| **Violations (≥50ms)** | **0 / 11,929 = 0.00%** |

**Assessment:** Consistent across sessions. ~0.5ms higher mean vs. Session 4 — within session-to-session variation. Clean baseline confirmed.

---

## 8. Attack Results

The attacker is PacketRusher running on `.2 PC`, targeting AMF at `192.168.10.4:38412` (iptables DNAT from .4 PC host to Docker container). Multiple IP aliases on eno1 allow parallel SCTP associations.

### 8.1 Single-Instance Attacks (Session 4)

**Attack 1 — 100 UEs, single burst (no loop)**

| Metric | Baseline | Attack 1 | Δ |
|--------|----------|---------|---|
| N | 11,929 | 11,899 | −30 |
| Mean OWD | 14.86 ms | 14.79 ms | −0.5% |
| p99 | 24.47 ms | 24.37 ms | −0.4% |
| **Max** | **25.77 ms** | **336.78 ms** | **+1205%** |
| **Violations (≥50ms)** | **0 (0.00%)** | **1 (0.01%)** | 1 packet |

**What happened:** PacketRusher sent 100 UE registrations in ~2 seconds (burst). gNB log shows `UEContextReleaseCommand` at this moment — the Quectel modem briefly lost its RRC context. It reconnected within ~4 seconds. The 336ms spike corresponds to packets dropped during RRC re-attachment, recovered only after HARQ retransmission succeeded. After reconnect, OWD returned to baseline.

**Attack 2 — 200 UEs, continuous loop (120s sustained)**

| Metric | Baseline | Attack 2 | Δ |
|--------|----------|---------|---|
| N | 11,929 | 11,930 | +1 |
| Mean OWD | 14.86 ms | 14.59 ms | −1.8% |
| p99 | 24.47 ms | 24.18 ms | −1.2% |
| Max | 25.77 ms | **24.96 ms** | −3% |
| **Violations (≥50ms)** | **0 (0.00%)** | **0 (0.00%)** | — |

**What happened:** With 200 UEs looping continuously (register → deregister → re-register every 100ms), AMF processes SUCI deconcealment continuously but at a manageable average rate. Measured Docker CPU: 0.92% (burst averages out). The 5G data path — which bypasses AMF entirely — was completely unaffected.

---

### 8.2 Scaled 4-Instance Attack (Session 5)

**Setup:** 4 IP aliases on .2 PC eno1 (192.168.10.22–.25), one PacketRusher per alias.  
**Load:** 4 × 100 UEs / 5s = **80 fake registrations/second**  
**AMF Docker CPU:** 1.11%

| Metric | Baseline | 4-Instance Attack | Δ |
|--------|----------|-------------------|---|
| N | 11,929 | 11,929 | 0 |
| Mean OWD | 15.32 ms | 15.29 ms | −0.2% |
| p99 | 24.91 ms | 24.85 ms | −0.2% |
| **Max** | **26.41 ms** | **34.12 ms** | **+29%** |
| Violations (≥50ms) | 0 (0.00%) | **0 (0.00%)** | — |
| Violations (≥30ms) | 0 (0.000%) | **2 (0.017%)** | attack caused 2 spikes |

**Key findings:**
- All 11,929 packets delivered — established data path fully resilient
- Max OWD increased +29% (26→34ms); 2 packets exceeded 30ms (none in baseline)
- AMF at 1.11% CPU is far from saturation at this scale
- **File:** `~/Desktop/attack_scaled_1778915784.csv`

---

### 8.3 Scaled 8-Instance Attack (Session 5)

**Setup:** 8 IP aliases on .2 PC eno1 (192.168.10.22–.29), one PacketRusher per alias.   
**Load:** 8 × 200 UEs / 1s = **1,600 fake registrations/second**  
**AMF Docker CPU:** 51.52%  
**.4 PC load average:** 9.37 (fully saturated on 8 cores)

**Sustained data path test (180s, established connection):**

| Metric | Value |
|--------|-------|
| Packets sent | 17,889 |
| Packets received | **17,889** |
| Packet loss | **0.00%** |
| Mean OWD | 15.08 ms |
| Max OWD | 33.67 ms |
| **Violations (≥50ms)** | **0 / 17,889 = 0.00%** |

**Reconnect time under attack:**

| Condition | Reconnect Time |
|-----------|---------------|
| Baseline (no attack) | **762 ms** |
| Under 8-instance attack (51% AMF CPU) | **841 ms** |
| Slowdown | **+10%** |

**gNB observations during 8-instance attack:**  
- RF underflows at 1–2/s (gNB at SCHED_OTHER, 65% CPU on one core, competing with ruby/parameter_bridge at 57% CPU)
- RNTI changed multiple times (0x4607 → 0x4609 → 0x4630) — UE context released and re-established
- `BearerContextInactivityNotification` fired when modem reconnected but sender wasn't yet active (fix: start sender before starting attack, to hold DRB)

**File:** `~/Desktop/attack_x8_measure_1778916720.csv`

---

## 9. Key Insight: Control Plane vs. Data Plane

The APE attack exhausts the **control plane** (AMF / NGAP). During **steady-state UDP transfer**, user packets flow entirely through the **data plane**:

```
RPi wwan0 → 5G radio → gNB → GTP-U (N3) → UPF → Docker bridge → receiver.py
```

**None of these nodes involve the AMF.** AMF is only invoked for:
1. Initial UE registration (once at attach time)
2. PDU session establishment / modification
3. RRC Release events requiring full re-registration

### Consequence for Paper Claims

| State | AMF load effect |
|-------|----------------|
| **Established data path (steady-state)** | **Zero measurable impact** — data plane bypasses AMF entirely |
| **New connection / reconnect** | Marginal delay (+10% reconnect time at 51% AMF CPU) |
| **Initial registration** | Attack delays first registration; PDU session setup slower under load |

### Why the srsUE Experiment (Sessions 1–3) Showed More Impact

With srsUE, PacketRusher ran on the **same machine** (.4 PC) as srsUE. PacketRusher competed for CPU with srsUE's non-RT threads (TASKWORKER, PHY_CMD). srsUE's control-plane threads are not RT-protected → CPU starvation → `Scheduling request failed` → `RRC Release` → 1.63% violations at 50ms threshold.

With the Quectel modem on RPi, there is clean physical separation: the attack runs on .2 PC, the UE is on RPi. The only shared resource is .4 PC (gNB + AMF). The gNB's RF scheduler is at SCHED_OTHER but the primary load on .4 PC comes from unrelated processes (ruby 57%), not the attack.

---

## 10. Summary Table

| Scenario | AMF CPU | Mean OWD | p99 | Max OWD | Violations (≥50ms) | Packet Loss |
|----------|---------|----------|-----|---------|-------------------|-------------|
| **Session 4 baseline (120s)** | ~0% | 14.86 ms | 24.47 ms | 25.77 ms | **0.00%** | 0% |
| Session 4 — 100 UE burst | ~1% | 14.79 ms | 24.37 ms | **336.78 ms** | **0.01%** (1 pkt) | 0.25% |
| Session 4 — 200 UE loop (120s) | 0.92% | 14.59 ms | 24.18 ms | 24.96 ms | **0.00%** | 0% |
| **Session 5 baseline (120s)** | ~0% | 15.32 ms | 24.91 ms | 26.41 ms | **0.00%** | 0% |
| Session 5 — 4-instance (120s) | 1.11% | 15.29 ms | 24.85 ms | **34.12 ms** | **0.00%** (2 >30ms) | 0% |
| Session 5 — 8-instance established (180s) | 51.52% | 15.08 ms | 24.67 ms | 33.67 ms | **0.00%** | **0.00%** |
| Reconnect — no attack | ~0% | — | — | — | — | 762ms downtime |
| Reconnect — 8-instance attack | 51.52% | — | — | — | — | 841ms downtime (+10%) |

---

## 12. Scripts and File Manifest

### Scripts (`~/urllc_bench/scripts/` on .2 PC)

| Script | Purpose |
|--------|---------|
| `preflight.sh` | Routing fixes on all 3 machines + wwan0 IP sync. Run after every reboot. |
| `setup_attack_aliases.sh` | One-time: add IP aliases 192.168.10.22–.29, create 8 PacketRusher configs |
| `launch_attack_scaled.sh` | Launch 4 parallel PacketRusher instances (aliases .22–.25) |
| `run_baseline.sh [dur]` | Full baseline run: starts receiver + runs sender on RPi (default 120s) |
| `run_attack.sh [dur]` | Full attack run: 4 attackers + receiver + sender + analysis |
| `monitor_attack.sh` | Live monitoring in a separate terminal |
| `analyze_csv.sh [name]` | Pulls latest CSV from .4 PC and prints stats |
| `killswitch.sh` | Stop all experiment processes on all machines |

### Result CSVs

| File | Description |
|------|-------------|
| `~/Desktop/rpi_quectel_baseline_1778880953.csv` | Session 4 clean baseline (120s, 11,929 pkts) |
| `~/Desktop/rpi_quectel_attack1_*.csv` | Session 4 attack — 100 UE burst |
| `~/Desktop/rpi_quectel_attack2_*.csv` | Session 4 attack — 200 UE loop |
| `.4 PC: ~/urllc_bench/results/baseline_1778915512.csv` | Session 5 clean baseline (120s, 11,929 pkts) |
| `~/Desktop/attack_scaled_1778915784.csv` | Session 5 — 4-instance attack (120s, 11,929 pkts) |
| `~/Desktop/attack_x8_measure_1778916720.csv` | Session 5 — 8-instance attack established (180s, 17,889 pkts) |
| `.4 PC: ~/urllc_bench/results/baseline_1778917126.csv` | Session 5 post-attack recovery baseline (60s, 5,964 pkts) |
