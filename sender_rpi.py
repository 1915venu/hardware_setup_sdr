#!/usr/bin/env python3
import socket, time, struct, argparse

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', required=True)
    parser.add_argument('--port', type=int, default=5005)
    parser.add_argument('--rate', type=float, default=100.0)
    parser.add_argument('--duration', type=int, default=120)
    args = parser.parse_args()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    interval = 1.0 / args.rate
    seq = 0
    end_time = time.time() + args.duration

    print(f"Sending to {args.host}:{args.port} at {args.rate}Hz for {args.duration}s")

    while time.time() < end_time:
        t_send = time.time()
        cmd = 1.0 if (int(t_send) % 4) < 3 else 0.0   # drive 3s, stop 1s
        payload = struct.pack('!Idf', seq, t_send, cmd)
        sock.sendto(payload, (args.host, args.port))
        seq += 1
        sleep_time = (t_send + interval) - time.time()
        if sleep_time > 0:
            time.sleep(sleep_time)

    sock.sendto(struct.pack('!Idf', 0xFFFFFFFF, time.time(), 0.0), (args.host, args.port))
    print(f"Done. Sent {seq} packets.")

if __name__ == '__main__':
    main()
