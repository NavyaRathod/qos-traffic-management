import csv
import os
import tempfile
import unittest
import uuid
from unittest.mock import patch

import client
import server
from traffic import (
    PACKET_HEADER,
    ResponseTracker,
    TRAFFIC_CLASSES,
    append_csv,
    calculate_rtt_ms,
    decode_packet,
    encode_packet,
    jain_fairness,
    packet_counts,
    positive_float,
    rtt_summary,
    throughput_mbps,
    valid_port,
)


class PacketTests(unittest.TestCase):
    def setUp(self):
        self.flow_id = uuid.uuid4().bytes

    def test_round_trip_encoding_preserves_class_sequence_timestamp_and_flow(self):
        packet = encode_packet("high", 12, 123456789, self.flow_id, 256)
        metadata = decode_packet(packet)

        self.assertEqual(len(packet), 256)
        self.assertEqual(metadata.traffic_class, "high")
        self.assertEqual(metadata.sequence, 12)
        self.assertEqual(metadata.timestamp_ns, 123456789)
        self.assertEqual(metadata.flow_id, self.flow_id)

    def test_all_classes_match_required_ports_and_identifiers(self):
        self.assertEqual(
            {name: config["port"] for name, config in TRAFFIC_CLASSES.items()},
            {"high": 5001, "medium": 5002, "low": 5003},
        )
        for traffic_class in TRAFFIC_CLASSES:
            packet = encode_packet(traffic_class, 0, 1, self.flow_id, PACKET_HEADER.size)
            self.assertEqual(decode_packet(packet).traffic_class, traffic_class)

    def test_invalid_or_short_datagrams_are_rejected(self):
        self.assertIsNone(decode_packet(b"short"))
        valid = encode_packet("low", 1, 2, self.flow_id, PACKET_HEADER.size)
        self.assertIsNone(decode_packet(b"BAD!" + valid[4:]))

    def test_encoder_rejects_invalid_class_size_sequence_and_flow_id(self):
        invalid_cases = (
            ("invalid", 0, 1, self.flow_id, PACKET_HEADER.size),
            ("high", -1, 1, self.flow_id, PACKET_HEADER.size),
            ("high", 0, 1, self.flow_id, PACKET_HEADER.size - 1),
            ("high", 0, 1, b"short", PACKET_HEADER.size),
        )
        for arguments in invalid_cases:
            with self.subTest(arguments=arguments):
                with self.assertRaises(ValueError):
                    encode_packet(*arguments)


class MeasurementTests(unittest.TestCase):
    def setUp(self):
        self.flow_id = uuid.uuid4().bytes

    def packet(self, traffic_class, sequence, timestamp_ns, flow_id=None):
        return encode_packet(
            traffic_class,
            sequence,
            timestamp_ns,
            self.flow_id if flow_id is None else flow_id,
            PACKET_HEADER.size,
        )

    def test_response_tracker_handles_out_of_order_duplicate_invalid_and_unexpected(self):
        tracker = ResponseTracker("high", self.flow_id)
        for sequence in range(3):
            tracker.register_send(sequence, 1000 + sequence)

        self.assertEqual(
            tracker.process_response(self.packet("high", 1, 1001), 2_001_000)[0],
            "received",
        )
        self.assertEqual(
            tracker.process_response(self.packet("high", 0, 1000), 2_000_000)[0],
            "out_of_order",
        )
        self.assertEqual(
            tracker.process_response(self.packet("high", 1, 1001), 2_002_000)[0],
            "duplicate",
        )
        self.assertEqual(tracker.process_response(b"bad", 2_003_000)[0], "invalid")
        self.assertEqual(
            tracker.process_response(self.packet("medium", 2, 1002), 2_004_000)[0],
            "unexpected",
        )
        self.assertEqual(
            tracker.process_response(self.packet("high", 99, 99), 2_005_000)[0],
            "unexpected",
        )
        self.assertEqual(len(tracker.received_times), 2)
        self.assertEqual(tracker.duplicate_packets, 1)
        self.assertEqual(tracker.out_of_order_packets, 1)
        self.assertEqual(tracker.invalid_packets, 1)
        self.assertEqual(tracker.unexpected_packets, 2)

    def test_packet_loss_counts_and_zero_sent_case(self):
        self.assertEqual(
            packet_counts(10, 7),
            {
                "packets_sent": 10,
                "packets_received": 7,
                "packets_lost": 3,
                "loss_percent": 30.0,
            },
        )
        self.assertEqual(packet_counts(0, 0)["loss_percent"], 0)
        with self.assertRaises(ValueError):
            packet_counts(2, 3)

    def test_throughput_and_rtt_calculations(self):
        self.assertAlmostEqual(throughput_mbps(1_000_000, 1), 8)
        self.assertAlmostEqual(calculate_rtt_ms(1_000_000, 3_000_000), 2)
        with self.assertRaises(ValueError):
            calculate_rtt_ms(2, 1)
        with self.assertRaises(ValueError):
            throughput_mbps(1, 0)

    def test_rtt_summary_is_empty_when_no_echoes_arrive(self):
        self.assertEqual(
            rtt_summary([]),
            {"avg_rtt_ms": None, "min_rtt_ms": None, "max_rtt_ms": None},
        )
        self.assertEqual(
            rtt_summary([2, 4]),
            {"avg_rtt_ms": 3, "min_rtt_ms": 2, "max_rtt_ms": 4},
        )

    def test_jain_fairness_all_classes_and_active_classes(self):
        self.assertAlmostEqual(jain_fairness([2, 2, 0]), 2 / 3)
        self.assertEqual(jain_fairness([2, 2]), 1)
        self.assertEqual(jain_fairness([0, 0, 0]), 0)
        self.assertEqual(jain_fairness([]), 0)
        with self.assertRaises(ValueError):
            jain_fairness([1, -1])


class CsvAndInputTests(unittest.TestCase):
    def test_client_accepts_both_packet_size_option_names(self):
        parser = client.build_parser()
        for option in ("--packet-size", "--payload-size"):
            with self.subTest(option=option):
                args = parser.parse_args([option, "512"])
                self.assertEqual(args.packet_size, 512)
        args = parser.parse_args(["--source-port", "5002"])
        self.assertEqual(args.source_port, 5002)

    def test_csv_appends_records_without_overwriting(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "results.csv")
            append_csv(path, {"record_type": "client_flow", "traffic_class": "high"})
            append_csv(path, {"record_type": "client_flow", "traffic_class": "low"})

            with open(path, newline="", encoding="utf-8") as result_file:
                rows = list(csv.DictReader(result_file))
            self.assertEqual(len(rows), 2)
            self.assertEqual(
                [row["traffic_class"] for row in rows], ["high", "low"]
            )

    def test_csv_rejects_incompatible_existing_header_and_unknown_columns(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "results.csv")
            with open(path, "w", newline="", encoding="utf-8") as result_file:
                result_file.write("old_header\n")
            with self.assertRaises(ValueError):
                append_csv(path, {"record_type": "client_flow"})
            with self.assertRaises(ValueError):
                append_csv(path + ".new", {"not_a_column": "value"})

    def test_validators_reject_out_of_range_values(self):
        self.assertEqual(valid_port("5001"), 5001)
        self.assertEqual(positive_float("1.5"), 1.5)
        for value in ("0", "-1", "nan", "inf"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    positive_float(value)
        for value in ("0", "65536", "bad"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    valid_port(value)


class ServerSummaryTests(unittest.TestCase):
    def test_fairness_summary_includes_inactive_class_as_zero_and_csv(self):
        class_stats = {name: server.new_stats() for name in TRAFFIC_CLASSES}
        for traffic_class, byte_count in (("high", 1000), ("medium", 1000)):
            stats = class_stats[traffic_class]
            stats["packets"] = 2
            stats["bytes"] = byte_count

        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "server.csv")
            with patch("builtins.print"):
                server.write_summary(
                    class_stats, {}, 5, "experiment-1", path, 0, 0
                )
            with open(path, newline="", encoding="utf-8") as result_file:
                rows = list(csv.DictReader(result_file))
            summary = next(
                row for row in rows if row["record_type"] == "server_fairness"
            )
            self.assertEqual(summary["active_classes"], "high,medium")
            self.assertAlmostEqual(
                float(summary["jain_fairness_all_classes"]), 2 / 3
            )
            self.assertEqual(
                float(summary["jain_fairness_active_classes"]), 1
            )
            self.assertEqual(
                float(summary["low_receive_throughput_mbps"]), 0
            )


if __name__ == "__main__":
    unittest.main()
