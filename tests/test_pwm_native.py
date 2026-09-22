"""Run the actual PWM + IO-model C sources against host hardware mocks.

Set PWM_TEST_CC (or CC) to an installed host C compiler. Tests skip explicitly
when no host compiler is available; the ESP-IDF cross-compiler cannot run them.
No compiler or dependency is downloaded.
"""
import ctypes
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
NATIVE = ROOT / "tests" / "native_pwm"


def host_compiler():
    configured = os.environ.get("PWM_TEST_CC") or os.environ.get("CC")
    if configured:
        if Path(configured).is_file():
            return [configured]
        return shlex.split(configured, posix=os.name != "nt")
    for name in ("cc", "gcc", "clang", "tcc"):
        path = shutil.which(name)
        if path:
            return [path]
    return None


class PwmFirmwareNativeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = host_compiler()
        if compiler is None:
            raise unittest.SkipTest("PWM native tests require an installed host C compiler (PWM_TEST_CC/CC).")
        cls.tempdir = tempfile.TemporaryDirectory(prefix="esp_io_pwm_")
        cls.addClassCleanup(cls.tempdir.cleanup)
        library = Path(cls.tempdir.name) / ("pwm_tests.dll" if os.name == "nt" else "pwm_tests.so")
        command = [
            *compiler, "-std=c99", "-shared",
            "-I", str(NATIVE), "-I", str(ROOT / "main"),
            str(ROOT / "main" / "io_model.c"),
            str(ROOT / "main" / "io_pwm.c"),
            str(NATIVE / "test_pwm.c"), "-o", str(library),
        ]
        if os.name != "nt":
            command.insert(len(compiler), "-fPIC")
        subprocess.run(command, check=True, capture_output=True, text=True)
        cls.library = ctypes.CDLL(str(library))
        if sys.platform == "win32":
            # Windows prevents removal while a DLL remains loaded.
            import _ctypes
            cls.addClassCleanup(_ctypes.FreeLibrary, cls.library._handle)

    def check_native(self, name):
        function = getattr(self.library, name)
        function.argtypes = []
        function.restype = ctypes.c_int
        failed_line = function()
        self.assertEqual(failed_line, 0, f"{name} failed at tests/native_pwm/test_pwm.c:{failed_line}")

    def test_defaults_and_disabled_configuration(self):
        self.check_native("test_defaults_and_disabled_configuration")

    def test_eight_channels_and_safe_reuse(self):
        self.check_native("test_eight_channels_and_safe_reuse")

    def test_four_frequencies_and_shared_timer_protection(self):
        self.check_native("test_four_frequencies_and_shared_timer_protection")

    def test_frequency_and_duty_boundaries(self):
        self.check_native("test_frequency_and_duty_boundaries")

    def test_static_endpoints_restart_and_readback(self):
        self.check_native("test_static_endpoints_restart_and_readback")

    def test_modes_coils_and_adc_do_not_steal_pwm(self):
        self.check_native("test_modes_coils_and_adc_do_not_steal_pwm")

    def test_timer_failure_preserves_configuration(self):
        self.check_native("test_timer_failure_preserves_configuration")

    def test_channel_failure_rolls_back_and_does_not_leak(self):
        self.check_native("test_channel_failure_rolls_back_and_does_not_leak")

    def test_stop_and_gpio_failures_preserve_active_state(self):
        self.check_native("test_stop_and_gpio_failures_preserve_active_state")


if __name__ == "__main__":
    unittest.main()
