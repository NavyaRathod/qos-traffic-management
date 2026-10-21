import socket
import argparse

parser = argparse.ArgumentParser()
parser.add_argument("--port", type=int, default=5004)
args = parser.parse_args()

HOST = "0.0.0.0"
PORT = args.port

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind((HOST, PORT))

print(f"UDP server listening on port {PORT}...")

packets = 0
bytes_received = 0

while True:
    data, address = sock.recvfrom(2048)
    packets += 1
    bytes_received += len(data)

    if packets % 100 == 0:
        print(
            f"Port {PORT}: received {packets} packets, "
            f"{bytes_received} bytes from {address}"
        )
