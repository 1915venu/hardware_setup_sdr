#!/usr/bin/env python3
import socket, time, struct, csv, os
import rclpy
from rclpy.node import Node
from geometry_msgs.msg import TwistStamped

DEADLINE_MS = 50.0

class ReceiverNode(Node):
    def __init__(self):
        super().__init__('urllc_receiver')
        self.pub = self.create_publisher(TwistStamped, '/cmd_vel', 10)

    def publish_cmd(self, linear_x):
        msg = TwistStamped()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.twist.linear.x = linear_x * 0.2
        self.pub.publish(msg)

def main():
    rclpy.init()
    node = ReceiverNode()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(('0.0.0.0', 5005))
    sock.settimeout(5.0)

    os.makedirs('results', exist_ok=True)
    csvfile = open(f'results/baseline_{int(time.time())}.csv', 'w', newline='')
    writer = csv.writer(csvfile)
    writer.writerow(['seq', 't_send', 't_recv', 'owd_ms', 'cmd', 'deadline_violated'])

    print("Listening on :5005 ...")
    pkt_count, violations = 0, 0

    try:
        while rclpy.ok():
            try:
                data, addr = sock.recvfrom(256)
            except socket.timeout:
                continue

            t_recv = time.time()
            seq, t_send, cmd = struct.unpack('!Idf', data)

            if seq == 0xFFFFFFFF:
                break

            owd_ms = (t_recv - t_send) * 1000.0
            violated = owd_ms > DEADLINE_MS
            if violated:
                violations += 1

            writer.writerow([seq, t_send, t_recv, f'{owd_ms:.3f}', f'{cmd:.1f}', int(violated)])
            node.publish_cmd(cmd)
            rclpy.spin_once(node, timeout_sec=0)
            pkt_count += 1

            if pkt_count % 500 == 0:
                print(f"Pkts: {pkt_count} | Violations: {violations} | OWD: {owd_ms:.1f}ms")

    except KeyboardInterrupt:
        pass
    finally:
        csvfile.close()
        node.destroy_node()
        rclpy.shutdown()
        print(f"Done. {pkt_count} packets, {violations} violations.")

if __name__ == '__main__':
    main()
