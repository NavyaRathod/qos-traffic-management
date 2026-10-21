import socket
import time
import argparse

parser = argparse.ArgumentParser(description="Send UDP traffic to the QoS UDP server.")
parser.add_argument("--host", default="10.0.0.2", help="Destination server IP (default: Mininet h2)")
parser.add_argument("--port", type=int, default=5004, help="UDP destination port: 5004 high, 5001 medium, 5006 low")
parser.add_argument("--rate", type=float, default=100, help="Approximate packets per second")
parser.add_argument("--seconds", type=int, default=5, help="How long to send")
args = parser.parse_args()

payload = b"QoS UDP test packet"
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
interval = 1.0 / max(args.rate, 1)

sent = 0
start = time.time()
end = start + args.seconds

while time.time() < end:
    sock.sendto(payload, (args.host, args.port))
    sent += 1
    time.sleep(interval)

sock.close()
print(f"Sent {sent} UDP packets to {args.host}:{args.port} in {args.seconds}s")
