# uRLLC 5G Testbed — Full Experiment Report
**Project:** NDSS Paper — APE Attack Against 5G AKA Protocol  
**Institution:** IIT Delhi  
**Conducted:** 2026-05-13 to 2026-05-15 
**Status:** Clean 120s baseline achieved. Attack demonstrated. Scaling blocked (see Section 7).

---

## Table of Contents

1. [Problem Statement](#1-problem-statement)
2. [Hardware Topology](#2-hardware-topology)
3. [Software Stack](#3-software-stack)
4. [Methodology](#4-methodology)
5. [System Tuning — The Tier System](#5-system-tuning--the-tier-system)
6. [Results](#6-results)
7. [Known Issues and Blockers](#7-known-issues-and-blockers)
8. [Next Steps](#8-next-steps)
9. [File & Script Manifest](#9-file--script-manifest)

---

## 1. Problem Statement

### The APE Attack

The **APE (Authentication Protocol Exhaustion)** attack targets the **5G AKA authentication protocol**. It floods the 5G Core's **N2 interface (NGAP)** with fake UE registration requests, forcing the UDM/SIDF to perform **SUCI deconcealment** (elliptic curve cryptography, expensive) for every fake UE. This exhausts authentication resources on the core.

### Why It Matters — Real-World Impact

Theoretical denial-of-service proofs are not enough for top-tier venues. This experiment provides **physical, measurable proof** that the attack causes real-world failures in a latency-critical application.

**The demonstration:**
- A TurtleBot3 robot in Gazebo is remotely operated at 100Hz over a **real physical 5G radio link**
- Movement commands have a **50ms deadline** — miss it and the robot cannot brake in time
- Under normal conditions: robot stops cleanly
- Under APE attack: OWD spikes, deadline violations accumulate, **robot overshoots and collides**

### The Metric

**One-Way Delay (OWD)** per UDP packet, measured end-to-end from sender to receiver across the 5G radio.

| Threshold | Meaning |
|-----------|---------|
| < 50ms | Deadline met, robot control valid |
| ≥ 50ms | **Deadline violated** — command arrives too late to stop the robot |

---

## 2. Hardware Topology

```
┌──────────────────────────────────────────────────────────────────┐
│  .2 PC  (venu @ 192.168.10.2)                                    │
│  Dell OptiPlex 5070 — Ubuntu 24.04 RT kernel                     │
│  USRP B210 (USB3) ◄──────────── 10 MHz FDD, Band 3, ARFCN 368500 │
│  srsUE  →  tun_srsue (10.45.1.2)                                 │
│  sender.py  →  UDP 100Hz  →  10.53.1.1:5005                      │
└────────────────────────┬─────────────────────────────────────────┘
                         │  NR-Uu radio (air)
                         ▼
┌──────────────────────────────────────────────────────────────────┐
│  .4 PC  (priyansh @ 192.168.10.4)                                │
│  Ubuntu 24.04 (default shell: zsh)                               │
│                                                                  │
│  ┌─────────────────────────────────────────────────────────────┐ │
│  │  Docker: open5gs_5gc container                              │ │
│  │  AMF  @ 10.53.1.2:38412 (NGAP/SCTP)                         │ │
│  │  UPF  → ogstun (10.45.0.x PDU IPs)                          │ │
│  └──────────────────┬──────────────────────────────────────────┘ │
│                     │  Docker bridge: 10.53.1.1 (gateway)        │
│  OCUDU gNB ─────────┘                                            │
│  receiver.py  ←  UDP  ←  ogstun  ←  UPF                          │
│  ROS2 Jazzy  →  /cmd_vel (TwistStamped)  →  Gazebo Harmonic      │
│  TurtleBot3 burger (simulated)                                   │
│  PacketRusher  →  NGAP flood  →  AMF                             │
└──────────────────────────────────────────────────────────────────┘
```

### Key Network Facts

| Detail | Value |
|--------|-------|
| Sender target IP | `10.53.1.1` (Docker bridge gateway) — **NOT** `10.45.0.1` (UPF intercepts it) |
| UE tunnel IP | `10.45.1.2` (assigned by Open5GS after PDU session) |
| AMF address (inside Docker) | `10.53.1.2:38412` |
| Radio bandwidth | 10 MHz  |
| USRP interface | USB 3.0 (bandwidth-limited, CPU-sensitive) |

### Open5GS / PLMN Credentials

| Parameter | Value |
|-----------|-------|
| PLMN | MCC=001, MNC=01 |
| TAC | 7, Slice SST=1 |
| IMSI | `001010123456780` |
| K | `00112233445566778899aabbccddeeff` |
| OPC | `63BFA50EE6523365FF14C1F45F88737D` |
| Home NW Public Key | curve25519, Profile A (scheme 1) |

---

## 3. Software Stack

### .2 PC (Sender / UE)

| Component | Details |
|-----------|---------|
| OS | Ubuntu 24.04 with real-time kernel |
| SDR driver | UHD (USRP Hardware Driver) |
| Radio software | srsRAN 4G — `srsue`, config `ue_rf_band3.conf` |
| Application | `sender.py` — pure Python, no ROS2 dependency |
| Scheduling | Both gnb and srsue launched with `sudo chrt -f 99` (SCHED_FIFO RT) |

### .4 PC (gNB + Core + Robotics)

| Component | Details |
|-----------|---------|
| OS | Ubuntu 24.04 (default shell: zsh — important, see Section 7) |
| gNB | OCUDU binary (`ocudu/build/apps/gnb/gnb`) |
| 5G Core | Open5GS via Docker (`open5gs_5gc`) |
| ROS2 | Jazzy Jalisco (Ubuntu 24.04 / Noble — Humble is 22.04 only) |
| Simulator | Gazebo Harmonic + TurtleBot3 Gazebo 2.3.7 |
| Application | `receiver.py` — UDP → TwistStamped → /cmd_vel |
| Attack tool | PacketRusher (`/home/priyansh/PacketRusher/`) |

### sender.py — How It Works

```
Every 10ms (100Hz):
  1. Record t_send = time.time()
  2. Compute cmd: 1.0 (drive forward) for 3s, 0.0 (stop) for 1s — repeating 4s cycle
  3. Pack: struct.pack('!Idf', seq, t_send, cmd)  → 20 bytes
  4. UDP sendto 10.53.1.1:5005
  5. sleep(remaining time in interval)   ← uses time.sleep(), NOT busy-wait
```

### receiver.py — How It Works

```
On each UDP packet:
  1. Unpack: seq, t_send, cmd = struct.unpack('!Idf', data)
  2. owd_ms = (time.time() - t_send) * 1000
  3. deadline_violated = 1 if owd_ms >= 50 else 0
  4. Write row to results/baseline_<timestamp>.csv
  5. Publish TwistStamped to /cmd_vel (drives TurtleBot3 in Gazebo)
  6. Print stats every 500 packets
```

### NTP Time Synchronization

OWD is measured across two different machines. For valid measurement:
- Both .2 PC and .4 PC run `chrony` / `systemd-timesyncd`
- Verified: `System time: 0.000000000 seconds slow of NTP time` (Stratum 3) on both
- Sub-millisecond clock sync — OWD measurement is valid

---

## 4. Methodology



### What Is Measured

- **OWD per packet** (ms): wall-clock time from `sendto()` on sender to `recvfrom()` on receiver
- **Packet delivery ratio**: packets received / packets sent
- **Jitter** (σ): standard deviation of OWD
- **Deadline violations**: packets with OWD ≥ 50ms, as a percentage

### The 50ms Deadline

Industrial robot control systems require deterministic sub-50ms latency. The TurtleBot3 at typical speed needs ~50ms to respond to a stop command. Past that threshold the robot is already in motion and cannot stop — causing a collision in simulation, which is the visual evidence in the paper.

### Run Protocol

Each experimental run:
1. Apply Tier 1 tuning on both machines (once per boot)
2. Start gNB on .4 PC → verify `Connected to AMF`
3. Start receiver on .4 PC → verify process alive
4. Start Tier 3 watcher on .2 PC → start srsUE → wait for `RRC Connected` + PDU Session
5. Run sender: `python3 sender.py --host 10.53.1.1 --rate 100 --duration 120`
6. Pull CSV from .4 PC, analyse statistics

For attack runs: start PacketRusher flood between Steps 4 and 5.

---

## 5. System Tuning — The Tier System

The untuned system had 43.9% deadline violations — unusable as a baseline. Three tiers of tuning were developed to isolate and eliminate host-side noise.

### Tier 1 — Host Stabilization (both machines)

Applied once per boot. Eliminates CPU frequency jitter, deep sleep-state latency, and socket buffer drops.

```bash
cpupower frequency-set -g performance   # lock CPU to max freq, no scaling
cpupower idle-set -D 0                  # disable C2/C3/C6 deep sleep states
sysctl -w vm.swappiness=0               # never swap — swap causes ms-level stalls
sysctl -w net.core.rmem_max=26214400    # 25MB socket receive buffer
sysctl -w net.core.wmem_max=26214400    # 25MB socket send buffer
sysctl -w net.core.rmem_default=2621440
sysctl -w net.core.wmem_default=2621440
sysctl -w net.core.netdev_max_backlog=5000
```

**Effect:** Eliminates OS-level scheduling jitter and network buffer drops.

### Tier 2 — CPU Core Isolation

Physically isolates cores 2 and 3 from the Linux scheduler. srsUE is pinned to these cores — no OS preemption possible.

```
/etc/default/grub:
GRUB_CMDLINE_LINUX="... isolcpus=2,3 nohz_full=2,3 rcu_nocbs=2,3
                         intel_idle.max_cstate=0 processor.max_cstate=0 idle=poll"
```

After reboot:
```bash
sudo taskset -c 2,3 chrt -f 99 srsue ue_rf_band3.conf
sudo taskset -c 4   chrt -f 50 python3 sender.py ...
```


### Tier 3 — TUN Interface Tuning (auto-applied on attach)

A watcher script (`tier3_on_tun_up.sh`) monitors for `tun_srsue` to appear and immediately applies:

```bash
ethtool -K tun_srsue gro off gso off tso off lro off
# ↑ Disable TCP offloading on the tunnel — causes batching/reordering of UDP

ip link set tun_srsue txqueuelen 50
# ↑ Default is 1000 — deep queue causes bufferbloat (observed: 2.7s stalls)

ip route add 10.53.1.1 dev tun_srsue
ip route add 10.45.0.0/16 dev tun_srsue
# ↑ Route sender traffic through the 5G tunnel

nohup ping -I tun_srsue 10.45.0.1 > /tmp/keepalive_ping.log 2>&1 &
# ↑ Mandatory: Open5GS releases RRC if UE is idle. Keepalive prevents RRC Release.
```

### ROS2 DDS Multicast Fix

Without this fix, ROS2 DDS discovery traffic (`239.255.0.x` multicast) leaks through `ogstun` to the UE, burning downlink radio resources and causing RF underflows.

```bash
# Both launch scripts now export:
export ROS_LOCALHOST_ONLY=1
```

This was the root cause of the Session 2 radio drop at t=19.4s.

### sender.py Busy-Wait Bug (Fixed Early)

The original sender used a busy-wait loop:
```python
while time.time() < next_time:
    pass   # ← pins one CPU core to 100%, causes thermal throttling
```

Fixed to:
```python
sleep_time = (t_send + interval) - time.time()
if sleep_time > 0:
    time.sleep(sleep_time)
```

**Effect:** Eliminated a 2.7s periodic bufferbloat spike caused by CPU thermal throttle.

---

## 6. Results

### 6.1 Session 1 — Untuned Baseline (120s)

**Date:** 2026-05-13  
**File:** `~/Desktop/baseline_1778753748.csv`  
**Conditions:** No host tuning, no DDS fix, busy-wait sender, no keepalive tuning

| Metric | Value |
|--------|-------|
| Packets | 11,854 |
| Duration | 120s |
| Mean OWD | **59.21 ms** |
| Std (jitter) | 55.69 ms |
| p50 | 41.39 ms |
| p90 | 129.13 ms |
| p99 | 256.02 ms |
| Max | 484.63 ms |
| **Violations (≥50ms)** | **5,202 / 11,854 = 43.9%** |

**Assessment:** Unusable as a baseline. Nearly half of all commands arrive too late for the robot. This is purely host-side noise — the radio itself is not the bottleneck at this stage.

**Root causes identified:**
- CPU frequency scaling causing millisecond-level processing jitter
- Deep C-states (C2/C3/C6) adding wake-up latency to every interrupt
- `tun_srsue` txqueuelen=1000 causing bufferbloat (up to 484ms)
- Sender busy-wait loop thermally throttling the CPU
- ROS2 DDS multicast consuming downlink radio bandwidth

---

### 6.2 Session 2 — Partially Tuned Run (19s, then radio dropped)

**Date:** 2026-05-14  
**File:** `~/Desktop/run_tuned_tier1_19s.csv`  
**Conditions:** Tier 1 + Tier 3 applied. DDS fix not yet applied. k3s still running.

| Metric | Value | Δ vs Untuned |
|--------|-------|-------------|
| Packets | 1,925 | (only 19.4s) |
| Mean OWD | **15.36 ms** | **−74%** |
| Std (jitter) | 16.71 ms | −70% |
| p50 | 11.08 ms | −73% |
| p99 | 107.94 ms | −58% |
| Max | 198.46 ms | −59% |
| **Violations (≥50ms)** | **31 / 1,925 = 1.6%** | **−96%** |

**What happened at t=19.4s:**  
The radio dropped — `srsue.log` showed:
```
RF status: O=0, U=1, L=12    ← UHD Underflow + Late packets
Scheduling request failed: releasing RRC connection...
Received RRC Release
```

**Root cause of drop:** ROS2 DDS multicast (`239.255.0.x`) was still leaking through `ogstun` to the UE. This consumed DL radio resources. Under sustained 100Hz load, combined with `k3s-server` using 8.2% CPU continuously, the srsUE radio worker threads were starved of CPU.

---

### 6.3 Session 3 — Clean Tuned Baseline (120s, full run)

**Date:** 2026-05-15  
**File:** `~/Desktop/baseline_1778757952.csv`  
**Conditions:** Tier 1 + Tier 3 + DDS fix (`ROS_LOCALHOST_ONLY=1`) + k3s stopped

| Metric | Value | Δ vs Untuned |
|--------|-------|-------------|
| Packets | **11,919** | full 120s |
| Duration | 120s | ✓ |
| Mean OWD | **12.36 ms** | **−79%** |
| Std (jitter) | 6.82 ms | −88% |
| p50 | 9.82 ms | −76% |
| p95 | — | — |
| p99 | **32.45 ms** | **−87%** |
| Max | **33.98 ms** | **−93%** |
| **Violations (≥50ms)** | **0 / 11,919 = 0.00%** | **−100%** |

**This is the paper baseline.** The radio held for the full 120 seconds with zero deadline violations.

---

### 6.4 Session 3 — APE Attack Run (Profile A, 50 fake UEs, same machine)

**Date:** 2026-05-15  
**File:** `~/Desktop/attack_profileA_1778791580.csv`  
**Attack:** PacketRusher `multi-ue -n 50 -tr 10 -td 200 -tbrr 10 --loopCount 0`  
**Conditions:** Same as 6.3, plus PacketRusher running on .4 PC

| Metric | Baseline (6.3) | Under Attack | Δ |
|--------|---------------|--------------|---|
| Packets | 11,919 | 11,925 | — |
| Mean OWD | 12.36 ms | 10.95 ms | −12% |
| Std (jitter) | 6.82 ms | 12.29 ms | **+80%** |
| p99 | 32.45 ms | **71.84 ms** | **+121%** |
| Max | 33.98 ms | **191.66 ms** | **+464%** |
| **Violations (≥50ms)** | **0 (0.00%)** | **194 (1.63%)** | **+∞** |

**Attack is real but mild.** The mean OWD is barely affected, but jitter and tail latency (p99, max) spike significantly, producing 194 deadline violations.

**Why only 1.63%:** See Section 7.1.

---

### 6.5 Summary Table

| Configuration | Mean OWD | p99 | Max | Violations |
|---------------|----------|-----|-----|-----------|
| Untuned (120s) | 59.21 ms | 256 ms | 484 ms | **43.9%** |
| Partial tuned, 19s | 15.36 ms | 108 ms | 198 ms | 1.6% |
| **Tuned baseline (120s)** | **12.36 ms** | **32.45 ms** | **33.98 ms** | **0.0%** |
| Attack — 50 UE same machine | 10.95 ms | 71.84 ms | 191 ms | **1.63%** |
| Attack — separate attacker (expected) | ~12 ms | >200 ms | >300 ms | **>30%** |
| Tuned + Tier 2 isolcpus (expected) | ~10 ms | ~28 ms | ~32 ms | 0.0% |

---

## 7. Known Issues and Blockers

### 7.1 Attack Effect Is Weak (1.63% violations)

**Root cause: PacketRusher and Open5GS share the same physical machine (.4 PC)**

```
.4 PC CPU budget:
  ├── gnb (SCHED_FIFO, RT priority)          ← needs ~30% CPU
  ├── Open5GS AMF+UDM (Docker)               ← processes SUCI deconcealment
  ├── PacketRusher (attacking the AMF)        ← generates fake UE registrations
  └── receiver.py + ROS2                      ← UDP processing

Problem: PacketRusher consuming CPU → AMF processes it → but PacketRusher
         itself cannot scale without also consuming more CPU on the same box.
         Self-throttling. The AMF never reaches saturation.
```

**How the attack actually works (mechanically):**  
Each fake UE triggers `Registration Request` → AMF calls UDM → UDM performs SUCI deconcealment (curve25519 ECDH, ~0.5ms per operation) → AMF CPU spikes → gnb CPU starved → RF scheduling degrades → OWD tail latency spikes.

**Why same-machine is insufficient:**  
With 50 fake UEs on the same box, PacketRusher itself consumes the CPU that would otherwise go to AMF processing. Net effect on the legitimate UE's path is indirect and small.

---


## 8. Next Steps

### Priority 1 — Stronger Attack 

Move PacketRusher to a separate machine or Rpi.

### Priority 2 — Quantitative Attack Sweep

Once the stronger attack path is established:
- Vary attacker UE count: 50, 100, 200, 500
- Vary `--rate` on sender: 50Hz, 100Hz
- Plot OWD CDF (baseline vs. attack overlay) for the paper figure

### Priority 3 — Video Recording

Connect HDMI dummy plug to .4 PC. After that, Gazebo + ffmpeg recording works without starving the radio. Record one baseline run (robot stops cleanly) and one attack run (robot collides)


## 9. File & Script Manifest

### Code Files (`.2 PC: ~/.gemini/antigravity/scratch/urllc_bench/`)

| File | Purpose |
|------|---------|
| [sender.py](sender.py) | 100Hz UDP sender. struct `!Idf`. `time.sleep()` pacing. |
| [receiver.py](receiver.py) | UDP → TwistStamped on /cmd_vel. ROS2 Jazzy. |
| [sender_lite.py](sender_lite.py) | No-ROS2 sender for pipeline sanity checks |
| [receiver_lite.py](receiver_lite.py) | No-ROS2 receiver for pipeline sanity checks |


### Persistent Scripts (`.2 PC: ~/urllc_bench/scripts/`)

| Script | Machine | What it does |
|--------|---------|-------------|
| `tier1.sh` | .2 PC | CPU governor, C-states, socket buffers |
| `tier3_on_tun_up.sh` | .2 PC | Watches tun_srsue, applies offloads/queue/routes/keepalive |
| `launch_srsue.sh` | .2 PC | `chrt -f 99 srsue ue_rf_band3.conf`, logs `/tmp/srsue.log` |
| `tier1_dot4.sh` | .4 PC | Same as tier1 but for .4 PC |
| `launch_gnb.sh` | .4 PC | `chrt -f 99 gnb`, logs `/tmp/gnb.log` |
| `launch_receiver.sh` | .4 PC | `receiver.py` with `ROS_LOCALHOST_ONLY=1` |
| `launch_gazebo.sh` | .4 PC | Gazebo with `ROS_LOCALHOST_ONLY=1`, real display `DISPLAY=:0` |
| `launch_gazebo_headless.sh` | .4 PC | Gazebo on Xvfb :99 + optional ffmpeg (do NOT use during radio) |

### Result CSV Files

| File | Description |
|------|-------------|
| `~/Desktop/baseline_1778753748.csv` | Untuned 120s baseline (Session 1) |
| `~/Desktop/run_tuned_tier1_19s.csv` | Partial tuned run, 19s (Session 2) |
| `~/Desktop/baseline_1778757952.csv` | **Clean tuned baseline 120s (Session 3) — USE THIS** |
| `~/Desktop/attack_profileA_1778791580.csv` | Attack run, 50 fake UEs same machine (Session 3) |

### PacketRusher

| Detail | Value |
|--------|-------|
| Location | `/home/priyansh/PacketRusher/` on .4 PC |
| Config | `/home/priyansh/PacketRusher/config/config.yml` |
| PLMN/AMF | Pre-configured for `001/01`, AMF at `10.53.1.2:38412` |

---

## Appendix — Bug Fixes Made During Setup

These bugs were in the original code and would have caused silent failures or crashes:

| # | Bug | Fix | Impact if unfixed |
|---|-----|-----|-------------------|
| 1 | `struct.pack('!IdF', ...)` — `F` is not a valid Python format code | Changed to `'!Idf'` in both sender and receiver | Crash on first packet |
| 2 | ROS2 Humble specified, but machines are Ubuntu 24.04 (Humble = 22.04 only) | All commands use **ROS2 Jazzy Jalisco** | Broken system dependencies |
| 3 | `receiver.py` published `geometry_msgs/msg/Twist` to `/cmd_vel` | Changed to `geometry_msgs/msg/TwistStamped` with `header.stamp` | Packets logged correctly but robot never moves |
| 4 | All SSH commands to .4 PC used bare `source setup.bash` under zsh | Wrapped in `bash -c "..."` | Every ROS2 command silently fails |
| 5 | sender.py used busy-wait loop `while ...: pass` | Changed to `time.sleep(remaining)` | CPU pinned at 100% → thermal throttle → 2.7s bufferbloat spikes |
| 6 | `ROS2 DDS` multicast leaked through `ogstun` to UE | `export ROS_LOCALHOST_ONLY=1` in all launch scripts | DL radio resources consumed by DDS discovery → RF underflows |
