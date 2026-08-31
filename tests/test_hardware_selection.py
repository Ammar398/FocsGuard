"""Provider-selection tests (spec section 46).

``select_provider`` is a pure function over an adapter list and a provider list,
so every hardware scenario can be tested on any machine - including the one that
matters most here: a box with the CUDA provider compiled in but no NVIDIA card
installed, which is precisely this development machine's situation.
"""

from __future__ import annotations

import pytest

from focusguard_agent.hardware.detector import select_provider
from focusguard_shared.constants import GpuVendor, InferenceProvider
from focusguard_shared.schemas import GpuInfo

CUDA = InferenceProvider.CUDA.value
DML = InferenceProvider.DIRECTML.value
CPU = InferenceProvider.CPU.value


def _gpu(vendor: GpuVendor, *, software: bool = False, name: str = "Test GPU") -> GpuInfo:
    vendor_ids = {
        GpuVendor.NVIDIA: 0x10DE,
        GpuVendor.AMD: 0x1002,
        GpuVendor.INTEL: 0x8086,
        GpuVendor.MICROSOFT: 0x1414,
        GpuVendor.UNKNOWN: 0x0000,
    }
    return GpuInfo(
        name=name,
        vendor=vendor,
        vendor_id=vendor_ids[vendor],
        device_id=0x0001,
        dedicated_memory_bytes=8 * 1024**3,
        is_software_adapter=software,
    )


AMD_GPU = _gpu(GpuVendor.AMD, name="AMD Radeon RX 580 2048SP")
NVIDIA_GPU = _gpu(GpuVendor.NVIDIA, name="NVIDIA GeForce RTX 3060")
SOFTWARE_GPU = _gpu(GpuVendor.MICROSOFT, software=True, name="Microsoft Basic Render Driver")


def test_amd_gpu_selects_directml() -> None:
    """This machine's actual configuration."""
    provider, _ = select_provider([AMD_GPU, SOFTWARE_GPU], [DML, CPU])
    assert provider is InferenceProvider.DIRECTML


def test_nvidia_gpu_selects_cuda_when_available() -> None:
    provider, _ = select_provider([NVIDIA_GPU], [CUDA, CPU])
    assert provider is InferenceProvider.CUDA


def test_cuda_is_refused_when_no_nvidia_adapter_present() -> None:
    """The central regression guard for this project.

    Leftover nvcuda.dll from a removed GPU can make a CUDA-enabled onnxruntime
    build advertise the provider on a machine with an AMD card. Choosing it would
    fail at the first inference call, in front of an examiner.
    """
    provider, notes = select_provider([AMD_GPU], [CUDA, DML, CPU])

    assert provider is InferenceProvider.DIRECTML
    assert any("no NVIDIA adapter is present" in note for note in notes)


def test_falls_back_to_cpu_without_onnxruntime() -> None:
    provider, notes = select_provider([AMD_GPU], [])
    assert provider is InferenceProvider.CPU
    assert any("onnxruntime is not installed" in note for note in notes)


def test_software_only_adapter_does_not_select_directml() -> None:
    """A basic render driver is not a GPU worth dispatching work to."""
    provider, _ = select_provider([SOFTWARE_GPU], [DML, CPU])
    assert provider is InferenceProvider.CPU


def test_no_adapters_falls_back_to_cpu() -> None:
    provider, _ = select_provider([], [DML, CPU])
    assert provider is InferenceProvider.CPU


def test_forced_provider_is_respected() -> None:
    """Used to verify the CPU path still works on a machine that has a GPU."""
    provider, notes = select_provider([AMD_GPU], [DML, CPU], force="CPUExecutionProvider")
    assert provider is InferenceProvider.CPU
    assert any("forced" in note.lower() for note in notes)


def test_forced_provider_falls_back_when_unavailable() -> None:
    provider, notes = select_provider([AMD_GPU], [CPU], force="CUDAExecutionProvider")
    assert provider is InferenceProvider.CPU
    assert any("not available" in note for note in notes)


@pytest.mark.parametrize("alias", ["cpu", "CPU", "CPUExecutionProvider"])
def test_forced_provider_accepts_friendly_aliases(alias: str) -> None:
    provider, _ = select_provider([AMD_GPU], [DML, CPU], force=alias)
    assert provider is InferenceProvider.CPU


def test_unknown_forced_provider_falls_back_to_auto_selection() -> None:
    provider, notes = select_provider([AMD_GPU], [DML, CPU], force="nonsense")
    assert provider is InferenceProvider.DIRECTML
    assert any("Unrecognised" in note for note in notes)
