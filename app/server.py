import argparse
from datetime import datetime, timezone
import selectors
import socket
import time
import uuid

from traffic import (
    TRAFFIC_CLASSES,
    append_csv,
    decode_packet,
    jain_fairness,
    nonnegative_float,
    throughput_mbps,
    valid_port,
)


def new_stats():
    return {
        "packets": 0,
        "bytes": 0,
        "duplicates": 0,
        "out_of_order": 0,
        "highest_sequence": -1,
        "sequences": set(),
    }


def measured_rate(stats, elapsed):
    return throughput_mbps(stats["bytes"], elapsed)


def write_summary(
    class_stats,
    flow_stats,
    elapsed,
    experiment_id,
    csv_path,
    invalid_packets,
    unexpected_packets,
):
    class_rates = {
        name: measured_rate(class_stats[name], elapsed)
        for name in TRAFFIC_CLASSES
    }
    active_rates = [
        class_rates[name]
        for name in TRAFFIC_CLASSES
        if class_stats[name]["packets"] > 0
    ]
    all_fairness = jain_fairness(
        [class_rates[name] for name in TRAFFIC_CLASSES]
    )
    active_fairness = jain_fairness(active_rates)
    active_classes = [
        name for name in TRAFFIC_CLASSES if class_stats[name]["packets"] > 0
    ]
    timestamp = datetime.now(timezone.utc).isoformat()

    for traffic_class, stats in class_stats.items():
        rate = class_rates[traffic_class]
        print(
            "class={} packets_received={} bytes={} receive_throughput={:.3f}Mbps".format(
                traffic_class, stats["packets"], stats["bytes"], rate
            )
        )
        if csv_path:
            append_csv(
                csv_path,
                {
                    "timestamp_utc": timestamp,
                    "experiment_id": experiment_id,
                    "record_type": "server_class",
                    "traffic_class": traffic_class,
                    "duration_s": elapsed,
                    "packets_received": stats["packets"],
                    "duplicate_packets": stats["duplicates"],
                    "out_of_order_packets": stats["out_of_order"],
                    "received_throughput_mbps": rate,
                },
            )

    print(
        "jain_fairness_all_classes={:.3f} "
        "jain_fairness_active_classes={:.3f} active_classes={}".format(
            all_fairness, active_fairness, len(active_classes)
        )
    )
    print(
        "Fairness compares measured throughput; it does not by itself indicate "
        "whether configured QoS priorities were met."
    )

    for (traffic_class, flow_id, address), stats in sorted(flow_stats.items()):
        rate = measured_rate(stats, elapsed)
        print(
            "flow class={} flow_id={} source={}:{} packets_received={} "
            "receive_throughput={:.3f}Mbps duplicates={} out_of_order={}".format(
                traffic_class,
                flow_id.hex(),
                address[0],
                address[1],
                stats["packets"],
                rate,
                stats["duplicates"],
                stats["out_of_order"],
            )
        )
        if csv_path:
            append_csv(
                csv_path,
                {
                    "timestamp_utc": timestamp,
                    "experiment_id": experiment_id,
                    "record_type": "server_flow",
                    "traffic_class": traffic_class,
                    "flow_id": flow_id.hex(),
                    "duration_s": elapsed,
                    "source_ip": address[0],
                    "source_port": address[1],
                    "packets_received": stats["packets"],
                    "duplicate_packets": stats["duplicates"],
                    "out_of_order_packets": stats["out_of_order"],
                    "received_throughput_mbps": rate,
                },
            )

    total_received = sum(stats["packets"] for stats in class_stats.values())
    if csv_path:
        summary = {
            "timestamp_utc": timestamp,
            "experiment_id": experiment_id,
            "record_type": "server_fairness",
            "duration_s": elapsed,
            "packets_received": total_received,
            "invalid_packets": invalid_packets,
            "unexpected_packets": unexpected_packets,
            "jain_fairness_all_classes": all_fairness,
            "jain_fairness_active_classes": active_fairness,
            "active_classes": ",".join(active_classes),
        }
        for traffic_class, rate in class_rates.items():
            summary[
                "{}_receive_throughput_mbps".format(traffic_class)
            ] = rate
        append_csv(csv_path, summary)


def build_parser():
    parser = argparse.ArgumentParser(
        description="Receive, classify, echo, and measure QoS UDP traffic."
    )
    parser.add_argument(
        "--ports",
        nargs="+",
        type=valid_port,
        help="UDP ports to listen on (defaults to all class ports)",
    )
    parser.add_argument(
        "--port",
        type=valid_port,
        help="Listen on one port instead of all class ports",
    )
    parser.add_argument(
        "--duration",
        type=nonnegative_float,
        default=0,
        help="Seconds to run; 0 runs until interrupted",
    )
    parser.add_argument("--csv", help="Append server measurements to this CSV file")
    parser.add_argument(
        "--experiment-id",
        default=None,
        help="Experiment ID shared with clients; generated if omitted",
    )
    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    if args.port is not None and args.ports is not None:
        parser.error("Use either --port or --ports, not both")

    if args.port is not None:
        ports = [args.port]
    elif args.ports is not None:
        ports = args.ports
    else:
        ports = [item["port"] for item in TRAFFIC_CLASSES.values()]
    if len(set(ports)) != len(ports):
        parser.error("Listening ports must be unique")

    port_classes = {
        config["port"]: traffic_class
        for traffic_class, config in TRAFFIC_CLASSES.items()
    }
    class_stats = {name: new_stats() for name in TRAFFIC_CLASSES}
    flow_stats = {}
    invalid_packets = 0
    unexpected_packets = 0
    experiment_id = args.experiment_id or uuid.uuid4().hex
    selector = selectors.DefaultSelector()
    sockets = []
    start_ns = time.monotonic_ns()
    deadline = (
        time.monotonic() + args.duration if args.duration > 0 else None
    )

    try:
        for port in ports:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sockets.append(sock)
            sock.setblocking(False)
            sock.bind(("0.0.0.0", port))
            selector.register(sock, selectors.EVENT_READ, port)

        print(
            "UDP server listening on ports {}; experiment_id={}".format(
                ", ".join(str(port) for port in ports), experiment_id
            ),
            flush=True,
        )

        while True:
            timeout = (
                None
                if deadline is None
                else max(0, deadline - time.monotonic())
            )
            if timeout == 0:
                break

            for key, _ in selector.select(timeout):
                data, address = key.fileobj.recvfrom(65535)
                metadata = decode_packet(data)
                if metadata is None:
                    invalid_packets += 1
                    continue

                expected_class = port_classes.get(key.data)
                if expected_class is not None and expected_class != metadata.traffic_class:
                    unexpected_packets += 1
                    continue

                flow_key = (
                    metadata.traffic_class,
                    metadata.flow_id,
                    address,
                )
                flow = flow_stats.setdefault(flow_key, new_stats())
                class_record = class_stats[metadata.traffic_class]
                if metadata.sequence in flow["sequences"]:
                    flow["duplicates"] += 1
                    class_record["duplicates"] += 1
                    continue

                if metadata.sequence < flow["highest_sequence"]:
                    flow["out_of_order"] += 1
                    class_record["out_of_order"] += 1
                flow["highest_sequence"] = max(
                    flow["highest_sequence"], metadata.sequence
                )
                flow["sequences"].add(metadata.sequence)
                for stats in (class_record, flow):
                    stats["packets"] += 1
                    stats["bytes"] += len(data)
                key.fileobj.sendto(data, address)
    except KeyboardInterrupt:
        print("\nStopping UDP server.")
    finally:
        elapsed = max(
            (time.monotonic_ns() - start_ns) / 1_000_000_000,
            1e-9,
        )
        selector.close()
        for sock in sockets:
            sock.close()

    write_summary(
        class_stats,
        flow_stats,
        elapsed,
        experiment_id,
        args.csv,
        invalid_packets,
        unexpected_packets,
    )
    print(
        "invalid_packets={} unexpected_packets={}".format(
            invalid_packets, unexpected_packets
        )
    )
    if args.csv:
        append_csv(
            args.csv,
            {
                "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                "experiment_id": experiment_id,
                "record_type": "server_errors",
                "duration_s": elapsed,
                "invalid_packets": invalid_packets,
                "unexpected_packets": unexpected_packets,
            },
        )


if __name__ == "__main__":
    main()
