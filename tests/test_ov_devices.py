"""A "GPU" setting means an Intel GPU, or CPU when there is none."""

import pytest

from ov_devices import resolve_device


class FakeCore:
    def __init__(self, devices, fail=False):
        self.devices = devices
        self.fail = fail

    @property
    def available_devices(self):
        if self.fail:
            raise RuntimeError("no plugins")
        return list(self.devices)

    def get_property(self, device, name):
        assert name == "FULL_DEVICE_NAME"
        return self.devices[device]


INTEL = "Intel(R) Arc(TM) 140V GPU (16GB) (iGPU)"
NVIDIA = "NVIDIA GeForce RTX 3090 (dGPU)"


@pytest.mark.parametrize("devices, expected", [
    ({"CPU": "cpu", "GPU": INTEL, "NPU": "Intel(R) AI Boost"}, "GPU"),
    ({"CPU": "cpu", "GPU": NVIDIA}, "CPU"),
    ({"CPU": "cpu", "GPU.0": NVIDIA, "GPU.1": INTEL}, "GPU.1"),
    ({"CPU": "cpu", "GPU.0": INTEL, "GPU.1": NVIDIA}, "GPU.0"),
    ({"CPU": "cpu"}, "CPU"),
])
def test_gpu_resolves_to_an_intel_gpu_or_cpu(devices, expected):
    lines = []
    assert resolve_device("GPU", FakeCore(devices), lines.append) == expected
    if expected != "GPU":
        assert lines, "a changed device is logged"


@pytest.mark.parametrize("device", ["CPU", "NPU", "GPU.1"])
def test_other_devices_pass_through_without_a_lookup(device):
    assert resolve_device(device, FakeCore({}, fail=True)) == device


def test_a_failed_lookup_keeps_gpu():
    lines = []
    assert resolve_device("GPU", FakeCore({}, fail=True), lines.append) == "GPU"
    assert "Could not list GPUs" in lines[0]
