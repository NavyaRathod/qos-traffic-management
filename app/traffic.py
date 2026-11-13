import csv
import math
import os
import struct
from collections import namedtuple


TRAFFIC_CLASSES = {
    "high": {"id": 1, "port": 5001},
    "medium": {"id": 2, "port": 5002},
    "low": {"id": 3, "port": 5003},
}
CLASS_NAMES_BY_ID = {
    traffic_class["id"]: name
    for name, traffic_class in TRAFFIC_CLASSES.items()
}
PACKET_MAGIC = b"QOS1"
PACKET_HEADER = struct.Struct("!4sBQQ16s")
PacketMetadata = namedtuple(
    "PacketMetadata", ("traffic_class", "sequence", "timestamp_ns", "flow_id")
)

CSV_FIELDS = (
    "timestamp_utc",
    "experiment_id",
    "record_type",
    "traffic_class",
    "flow_id",
    "destination_ip",
    "destination_port",
    "source_ip",
    "source_port",
    "packet_size_bytes",
    "target_rate_pps",
    "duration_s",
    "packets_sent",
    "packets_received",
    "packets_lost",
    "duplicate_packets",
    "out_of_order_packets",
    "invalid_packets",
    "unexpected_packets",
    "loss_percent",
    "offered_throughput_mbps",
    "received_throughput_mbps",
    "echo_throughput_mbps",
    "avg_rtt_ms",
    "min_rtt_ms",
    "max_rtt_ms",
    "jain_fairness_all_classes",
    "jain_fairness_active_classes",
    "active_classes",
    "high_receive_throughput_mbps",
    "medium_receive_throughput_mbps",
    "low_receive_throughput_mbps",
)


def positive_float(value):
    parsed = float(value)
    if not math.isfinite(parsed) or parsed <= 0:
        raise ValueError("Value must be finite and greater than zero.")
    return parsed


def nonnegative_float(value):
    parsed = float(value)
    if not math.isfinite(parsed) or parsed < 0:
        raise ValueError("Value must be finite and zero or greater.")
    return parsed


def valid_port(value):
    parsed = int(value)
    if parsed < 1 or parsed > 65535:
        raise ValueError("Port must be between 1 and 65535.")
    return parsed


def encode_packet(traffic_class, sequence, timestamp_ns, flow_id, packet_size):
    if traffic_class not in TRAFFIC_CLASSES:
        raise ValueError("Unknown traffic class: {}".format(traffic_class))
    if not isinstance(sequence, int) or not 0 <= sequence < 2**64:
        raise ValueError("Sequence number must be an unsigned 64-bit integer.")
    if not isinstance(timestamp_ns, int) or not 0 <= timestamp_ns < 2**64:
        raise ValueError("Timestamp must be an unsigned 64-bit integer.")
    if not isinstance(flow_id, bytes) or len(flow_id) != 16:
        raise ValueError("Flow ID must contain exactly 16 bytes.")
    if not isinstance(packet_size, int) or not PACKET_HEADER.size <= packet_size <= 65507:
        raise ValueError(
            "Packet size must be between {} and 65507 bytes.".format(
                PACKET_HEADER.size
            )
        )

    header = PACKET_HEADER.pack(
        PACKET_MAGIC,
        TRAFFIC_CLASSES[traffic_class]["id"],
        sequence,
        timestamp_ns,
        flow_id,
    )
    return header + bytes(packet_size - PACKET_HEADER.size)


def decode_packet(data):
    if len(data) < PACKET_HEADER.size:
        return None

    magic, class_id, sequence, timestamp_ns, flow_id = PACKET_HEADER.unpack_from(data)
    if magic != PACKET_MAGIC or class_id not in CLASS_NAMES_BY_ID:
        return None

    return PacketMetadata(
        CLASS_NAMES_BY_ID[class_id], sequence, timestamp_ns, flow_id
    )


class ResponseTracker:
    def __init__(self, traffic_class, flow_id):
        self.traffic_class = traffic_class
        self.flow_id = flow_id
        self.sent_times = {}
        self.received_times = {}
        self.highest_sequence = -1
        self.duplicate_packets = 0
        self.out_of_order_packets = 0
        self.invalid_packets = 0
        self.unexpected_packets = 0

    def register_send(self, sequence, timestamp_ns):
        if sequence in self.sent_times:
            raise ValueError("Sequence number was already sent.")
        self.sent_times[sequence] = timestamp_ns

    def process_response(self, data, received_at_ns):
        metadata = decode_packet(data)
        if metadata is None:
            self.invalid_packets += 1
            return "invalid", None

        if (
            metadata.traffic_class != self.traffic_class
            or metadata.flow_id != self.flow_id
            or metadata.sequence not in self.sent_times
            or metadata.timestamp_ns != self.sent_times.get(metadata.sequence)
        ):
            self.unexpected_packets += 1
            return "unexpected", None

        sequence = metadata.sequence
        if sequence in self.received_times:
            self.duplicate_packets += 1
            return "duplicate", None

        status = "received"
        if sequence < self.highest_sequence:
            self.out_of_order_packets += 1
            status = "out_of_order"
        self.highest_sequence = max(self.highest_sequence, sequence)
        self.received_times[sequence] = received_at_ns
        return status, calculate_rtt_ms(
            self.sent_times[sequence], received_at_ns
        )


def packet_counts(sent, received):
    if not isinstance(sent, int) or not isinstance(received, int):
        raise ValueError("Packet counts must be integers.")
    if sent < 0 or received < 0 or received > sent:
        raise ValueError("Packet counts must satisfy 0 <= received <= sent.")
    lost = sent - received
    return {
        "packets_sent": sent,
        "packets_received": received,
        "packets_lost": lost,
        "loss_percent": (lost / sent * 100) if sent else 0.0,
    }


def throughput_mbps(byte_count, duration_seconds):
    if not isinstance(byte_count, int) or byte_count < 0:
        raise ValueError("Byte count must be a nonnegative integer.")
    if not math.isfinite(duration_seconds) or duration_seconds <= 0:
        raise ValueError("Duration must be finite and greater than zero.")
    return byte_count * 8 / (duration_seconds * 1_000_000)


def calculate_rtt_ms(sent_at_ns, received_at_ns):
    if received_at_ns < sent_at_ns:
        raise ValueError("Receive timestamp cannot precede send timestamp.")
    return (received_at_ns - sent_at_ns) / 1_000_000


def rtt_summary(samples_ms):
    samples = list(samples_ms)
    if not samples:
        return {"avg_rtt_ms": None, "min_rtt_ms": None, "max_rtt_ms": None}
    if any(not math.isfinite(sample) or sample < 0 for sample in samples):
        raise ValueError("RTT samples must be finite and nonnegative.")
    return {
        "avg_rtt_ms": sum(samples) / len(samples),
        "min_rtt_ms": min(samples),
        "max_rtt_ms": max(samples),
    }


def jain_fairness(throughputs):
    rates = list(throughputs)
    if any(not math.isfinite(rate) or rate < 0 for rate in rates):
        raise ValueError("Throughputs must be finite and nonnegative.")
    if not rates or not any(rates):
        return 0.0
    total = sum(rates)
    return total * total / (len(rates) * sum(rate * rate for rate in rates))


def append_csv(path, row):
    unknown_fields = set(row) - set(CSV_FIELDS)
    if unknown_fields:
        raise ValueError(
            "Unknown CSV fields: {}".format(", ".join(sorted(unknown_fields)))
        )

    file_exists = os.path.exists(path) and os.path.getsize(path) > 0
    if file_exists:
        with open(path, "r", newline="", encoding="utf-8") as existing:
            header = next(csv.reader(existing), [])
        if tuple(header) != CSV_FIELDS:
            raise ValueError(
                "CSV header does not match the QoS results schema: {}".format(path)
            )

    with open(path, "a", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=CSV_FIELDS)
        if not file_exists:
            writer.writeheader()
        writer.writerow(
            {
                field: "" if row.get(field) is None else row.get(field, "")
                for field in CSV_FIELDS
            }
        )
