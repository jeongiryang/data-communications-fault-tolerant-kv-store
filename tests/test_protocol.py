import socket
import unittest

from kvstore.common.models import Message, MessageType
from kvstore.common.protocol import ProtocolError, receive_message, send_message


class ProtocolTests(unittest.TestCase):
    def test_message_round_trip_over_socket(self) -> None:
        left, right = socket.socketpair()
        self.addCleanup(left.close)
        self.addCleanup(right.close)
        expected = Message(
            message_type=MessageType.QUEUE_STATUS,
            sender_id="worker-1",
            request_id="request-1",
            payload={"queue_size": 7},
        )

        send_message(left, expected)
        self.assertEqual(receive_message(right), expected)

    def test_oversized_frame_header_is_rejected(self) -> None:
        left, right = socket.socketpair()
        self.addCleanup(left.close)
        self.addCleanup(right.close)
        left.sendall((1_048_577).to_bytes(4, "big"))

        with self.assertRaises(ProtocolError):
            receive_message(right)


if __name__ == "__main__":
    unittest.main()
