from __future__ import annotations

import os
import signal
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = PROJECT_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import runtime_relocation_audit_launcher_g1_r4 as R4


class ProcStateParserTest(unittest.TestCase):
    def test_running_state_is_not_mistaken_for_stopped(self) -> None:
        state = R4._parse_proc_state("Name:\tabacus\nState:\tR (running)\n")
        self.assertEqual(state, "R")
        self.assertNotIn(state, R4.STOPPED_PROC_STATES)

    def test_upper_and_lower_stopped_states_are_accepted(self) -> None:
        for state in ("T", "t"):
            with self.subTest(state=state):
                parsed = R4._parse_proc_state(f"State:\t{state} (stopped)\n")
                self.assertIn(parsed, R4.STOPPED_PROC_STATES)

    def test_missing_duplicate_and_malformed_state_are_rejected(self) -> None:
        for status in (
            "Name:\tabacus\n",
            "State:\tR\nState:\tT\n",
            "State:\trunning\n",
        ):
            with self.subTest(status=status):
                with self.assertRaises(ValueError):
                    R4._parse_proc_state(status)

    def test_wait_observes_transition_instead_of_key_name(self) -> None:
        with (
            mock.patch.object(R4, "_read_proc_state", side_effect=["R", "S", "t"]),
            mock.patch.object(R4.time, "monotonic", side_effect=[1.0, 1.1, 1.2]),
            mock.patch.object(R4.time, "sleep"),
        ):
            state, failure = R4._wait_for_proc_stop(17, 2.0)
        self.assertEqual(state, "t")
        self.assertIsNone(failure)

    def test_wait_timeout_reports_last_nonstopped_state(self) -> None:
        with (
            mock.patch.object(R4, "_read_proc_state", return_value="R"),
            mock.patch.object(R4.time, "monotonic", side_effect=[1.0, 2.0]),
            mock.patch.object(R4.time, "sleep"),
        ):
            state, failure = R4._wait_for_proc_stop(17, 1.5)
        self.assertIsNone(state)
        self.assertEqual(failure, "stop_confirmation_timeout:last_state:R")


class ReleaseAndCaptureTest(unittest.TestCase):
    def setUp(self) -> None:
        self.handshake_context = tempfile.TemporaryDirectory()
        self.handshake = Path(self.handshake_context.name)
        self.expected = Path("/recovery/abacus")
        self.old = Path("/old")
        self.recovery = Path("/recovery")

    def tearDown(self) -> None:
        self.handshake_context.cleanup()

    def _invoke(self, processes: dict[int, dict] | None = None) -> str | None:
        return R4._release_and_capture_rank_g1_r4(
            2,
            123,
            self.handshake,
            self.expected,
            self.old,
            self.recovery,
            {} if processes is None else processes,
            {},
            5.0,
        )

    def test_stop_timeout_never_captures_maps_and_resumes(self) -> None:
        with (
            mock.patch.object(R4._r1, "_atomic_control_token"),
            mock.patch.object(R4._r1, "_executable", return_value=self.expected),
            mock.patch.object(R4._r1, "_capture_target") as capture,
            mock.patch.object(
                R4,
                "_wait_for_proc_stop",
                return_value=(None, "stop_confirmation_timeout:last_state:R"),
            ),
            mock.patch.object(R4.time, "monotonic", return_value=100.0),
            mock.patch.object(R4.os, "kill") as kill,
        ):
            failure = self._invoke()
        self.assertEqual(failure, "rank_2_stop_confirmation_timeout:last_state:R")
        capture.assert_not_called()
        self.assertEqual(
            kill.call_args_list,
            [mock.call(123, signal.SIGSTOP), mock.call(123, signal.SIGCONT)],
        )

    def test_capture_is_bracketed_by_confirmed_stopped_states(self) -> None:
        processes: dict[int, dict] = {}

        def capture(*args: object) -> bool:
            processes[123] = {"pid": 123, "initial_map_capture_observed": True}
            return True

        with (
            mock.patch.object(R4._r1, "_atomic_control_token"),
            mock.patch.object(R4._r1, "_executable", return_value=self.expected),
            mock.patch.object(R4._r1, "_capture_target", side_effect=capture),
            mock.patch.object(R4, "_wait_for_proc_stop", return_value=("t", None)),
            mock.patch.object(R4, "_post_capture_state_failure", return_value=("t", None)),
            mock.patch.object(R4.time, "monotonic", return_value=100.0),
            mock.patch.object(R4.os, "kill") as kill,
        ):
            failure = self._invoke(processes)
        self.assertIsNone(failure)
        self.assertTrue(processes[123]["initial_map_capture_stop_confirmed"])
        self.assertEqual(processes[123]["initial_map_capture_stop_state_before"], "t")
        self.assertEqual(processes[123]["initial_map_capture_stop_state_after"], "t")
        self.assertEqual(kill.call_count, 2)

    def test_stop_lost_after_capture_is_rejected(self) -> None:
        processes = {123: {"pid": 123}}
        with (
            mock.patch.object(R4._r1, "_atomic_control_token"),
            mock.patch.object(R4._r1, "_executable", return_value=self.expected),
            mock.patch.object(R4._r1, "_capture_target", return_value=True),
            mock.patch.object(R4, "_wait_for_proc_stop", return_value=("T", None)),
            mock.patch.object(
                R4,
                "_post_capture_state_failure",
                return_value=("R", "stop_lost_during_map_capture:state:R"),
            ),
            mock.patch.object(R4.time, "monotonic", return_value=100.0),
            mock.patch.object(R4.os, "kill"),
        ):
            failure = self._invoke(processes)
        self.assertEqual(failure, "rank_2_stop_lost_during_map_capture:state:R")


@unittest.skipUnless(
    sys.platform.startswith("linux") and Path("/proc").is_dir(),
    "Linux /proc required",
)
class LinuxProcStopIntegrationTest(unittest.TestCase):
    def test_real_sigstop_is_observed_before_proc_maps_read(self) -> None:
        process = subprocess.Popen(["/bin/sleep", "30"])
        try:
            initial = R4._read_proc_state(process.pid)
            self.assertNotIn(initial, R4.STOPPED_PROC_STATES)
            os.kill(process.pid, signal.SIGSTOP)
            state, failure = R4._wait_for_proc_stop(
                process.pid, R4.time.monotonic() + 2.0
            )
            self.assertIsNone(failure)
            self.assertIn(state, R4.STOPPED_PROC_STATES)
            self.assertTrue((Path("/proc") / str(process.pid) / "maps").read_text())
            os.kill(process.pid, signal.SIGCONT)
        finally:
            try:
                os.kill(process.pid, signal.SIGCONT)
            except ProcessLookupError:
                pass
            process.terminate()
            process.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()
