import threading
import unittest

from kvstore.master.stub import RegistrationServer
from kvstore.worker.smoke_client import register_worker


class RegistrationStubTests(unittest.TestCase):
    def test_worker_can_register_over_tcp(self) -> None:
        server = RegistrationServer(("127.0.0.1", 0))
        self.addCleanup(server.server_close)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.shutdown)
        host, port = server.server_address

        response = register_worker(
            worker_id="worker-1",
            master_host=host,
            master_port=port,
            p2p_host="127.0.0.1",
            p2p_port=6001,
        )

        self.assertEqual(response.payload["worker_id"], "worker-1")
        self.assertIn("worker-1", server.registration_state.snapshot())


if __name__ == "__main__":
    unittest.main()
