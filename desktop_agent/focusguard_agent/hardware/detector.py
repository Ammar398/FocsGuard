"""Hardware detection and inference-provider selection (spec section 46).

THE RULE THIS MODULE EXISTS TO ENFORCE
--------------------------------------
Check which GPU is physically present *before* trusting any GPU library.

That sounds obvious, but it is exactly what most detection code gets wrong. This
development machine carries ``nvcuda.dll`` and ``nvml.dll`` in System32, plus
registry entries for a GeForce GTX 750 Ti, left behind by a graphics card that
has since been removed. The card actually installed is an AMD Radeon RX 580. Any
detector that reasons "nvcuda.dll exists, therefore CUDA works" reports CUDA
support on a machine with no NVIDIA hardware at all - and then fails in front of
an examiner.

So the order is always: enumerate real adapters through DXGI, establish the
vendor, and only then consider CUDA. When a leftover NVIDIA library is found it
is recorded in ``notes`` and explicitly disregarded, rather than silently
ignored, so the reasoning is visible in the start-up banner.

SELECTION LADDER
----------------
1. CUDA      - only if an NVIDIA adapter is present AND the provider is built in.
2. DirectML  - any Direct3D 12 GPU. This is the path used on this machine, and
               it is what lets the 8 GB RX 580 run YOLO instead of the CPU.
3. CPU       - always available, always works.

Nothing here may raise. A failed hardware probe degrades to CPU (spec section 46:
"Never crash if CUDA is unavailable").
"""

from __future__ import annotations

import platform
import sys
from pathlib import Path

from focusguard_agent.hardware.dxgi import enumerate_adapters, has_vendor
from focusguard_shared.constants import (
    PROVIDER_PREFERENCE,
    GpuVendor,
    InferenceProvider,
)
from focusguard_shared.schemas import CpuInfo, GpuInfo, HardwareReport
from focusguard_shared.utilities import get_logger

logger = get_logger("hardware.detector")

#: Presence of these files proves an NVIDIA driver was installed at some point.
#: It does NOT prove an NVIDIA GPU is present now.
_NVIDIA_DRIVER_MARKERS: tuple[str, ...] = ("nvcuda.dll", "nvml.dll")


def _detect_cpu() -> CpuInfo:
    """Read CPU details, preferring the human-readable registry name.

    ``platform.processor()`` returns "Intel64 Family 6 Model 94 Stepping 3" on
    Windows, which is useless in a demo banner. The registry holds the marketing
    name the user recognises: "Intel(R) Core(TM) i5-6500 CPU @ 3.20GHz".
    """
    name = platform.processor() or platform.machine() or "Unknown CPU"

    if sys.platform == "win32":
        try:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"HARDWARE\DESCRIPTION\System\CentralProcessor\0",
            ) as key:
                value, _ = winreg.QueryValueEx(key, "ProcessorNameString")
                if value:
                    name = str(value).strip()
        except OSError:
            logger.debug("Could not read ProcessorNameString from registry", exc_info=True)

    physical = logical = 0
    frequency = 0.0
    try:
        import psutil

        physical = psutil.cpu_count(logical=False) or 0
        logical = psutil.cpu_count(logical=True) or 0
        freq = psutil.cpu_freq()
        if freq is not None:
            frequency = float(freq.max or freq.current or 0.0)
    except Exception:  # noqa: BLE001 - psutil is optional for detection
        logger.debug("psutil CPU query failed", exc_info=True)

    return CpuInfo(
        name=name,
        physical_cores=physical,
        logical_cores=logical,
        max_frequency_mhz=frequency,
    )


def _detect_ram_bytes() -> int:
    try:
        import psutil

        return int(psutil.virtual_memory().total)
    except Exception:  # noqa: BLE001
        logger.debug("psutil memory query failed", exc_info=True)
        return 0


def _onnxruntime_providers() -> tuple[list[str], str | None]:
    """Return the providers ONNX Runtime reports, and its version.

    Import failure is not an error at this stage: the detector must still produce
    a useful report on a machine where the inference stack is not yet installed.
    """
    try:
        import onnxruntime as ort

        return list(ort.get_available_providers()), str(ort.__version__)
    except ImportError:
        logger.debug("onnxruntime is not installed")
        return [], None
    except Exception:  # noqa: BLE001
        logger.warning("onnxruntime provider query failed", exc_info=True)
        return [], None


def _find_stale_nvidia_drivers() -> list[str]:
    """Return NVIDIA driver files present on disk, if any."""
    if sys.platform != "win32":
        return []

    system32 = Path("C:/Windows/System32")
    return [marker for marker in _NVIDIA_DRIVER_MARKERS if (system32 / marker).exists()]


def select_provider(
    gpus: list[GpuInfo],
    available_providers: list[str],
    force: str | None = None,
) -> tuple[InferenceProvider, list[str]]:
    """Choose an execution provider. Returns the choice and explanatory notes."""
    notes: list[str] = []

    if force:
        wanted = force.lower()
        for provider in PROVIDER_PREFERENCE:
            if wanted in (provider.value.lower(), provider.name.lower()):
                unavailable = (
                    provider is not InferenceProvider.CPU
                    and provider.value not in available_providers
                )
                if unavailable:
                    notes.append(
                        f"Forced provider {provider.value} is not available; using CPU instead."
                    )
                    return InferenceProvider.CPU, notes
                notes.append(f"Provider forced to {provider.value} by configuration.")
                return provider, notes
        notes.append(f"Unrecognised force_provider {force!r}; falling back to auto-selection.")

    has_nvidia = has_vendor(gpus, GpuVendor.NVIDIA)
    has_hardware_gpu = any(not gpu.is_software_adapter for gpu in gpus)

    # CUDA is considered only when NVIDIA hardware is actually present.
    if InferenceProvider.CUDA.value in available_providers:
        if has_nvidia:
            return InferenceProvider.CUDA, notes
        notes.append(
            "CUDA execution provider is built in, but no NVIDIA adapter is present. "
            "Ignoring it."
        )

    if InferenceProvider.DIRECTML.value in available_providers and has_hardware_gpu:
        return InferenceProvider.DIRECTML, notes

    if not available_providers:
        notes.append("onnxruntime is not installed; falling back to CPU.")
    elif InferenceProvider.DIRECTML.value not in available_providers:
        notes.append(
            "DirectML execution provider is not available in this onnxruntime build. "
            "Install onnxruntime-directml for GPU acceleration."
        )

    return InferenceProvider.CPU, notes


def detect_hardware(force_provider: str | None = None) -> HardwareReport:
    """Produce the full hardware report used at agent start-up."""
    gpus = enumerate_adapters()
    available_providers, ort_version = _onnxruntime_providers()

    selected, notes = select_provider(gpus, available_providers, force=force_provider)

    has_nvidia = has_vendor(gpus, GpuVendor.NVIDIA)
    cuda_available = has_nvidia and InferenceProvider.CUDA.value in available_providers
    directml_available = InferenceProvider.DIRECTML.value in available_providers

    # Surface the leftover-driver trap explicitly rather than staying quiet.
    stale = _find_stale_nvidia_drivers()
    if stale and not has_nvidia:
        notes.append(
            f"Found NVIDIA driver files ({', '.join(stale)}) but no NVIDIA adapter is "
            "installed - these are leftovers from a removed GPU and were disregarded."
        )

    if not gpus:
        notes.append("No display adapter could be enumerated; using CPU inference.")

    return HardwareReport(
        cpu=_detect_cpu(),
        total_ram_bytes=_detect_ram_bytes(),
        gpus=gpus,
        cuda_available=cuda_available,
        directml_available=directml_available,
        available_providers=available_providers,
        selected_provider=selected,
        onnxruntime_version=ort_version,
        notes=notes,
    )


def format_report(report: HardwareReport) -> str:
    """Render the start-up banner required by spec section 46."""
    lines: list[str] = [
        "=" * 62,
        "FocusGuard Hardware Detection",
        "=" * 62,
        f"  CPU      : {report.cpu}",
        f"  RAM      : {report.total_ram_gb:g} GB",
    ]

    if report.gpus:
        for index, gpu in enumerate(report.gpus):
            label = "GPU      " if index == 0 else "         "
            suffix = "  [software adapter]" if gpu.is_software_adapter else ""
            lines.append(f"  {label}: {gpu}{suffix}")
    else:
        lines.append("  GPU      : none detected")

    lines += [
        f"  CUDA     : {'Available' if report.cuda_available else 'Not available'}",
        f"  DirectML : {'Available' if report.directml_available else 'Not available'}",
        f"  ONNX RT  : {report.onnxruntime_version or 'not installed'}",
        "-" * 62,
        f"  Selected inference provider: {report.selected_provider.value}",
        "=" * 62,
    ]

    if report.notes:
        lines.append("Notes:")
        lines.extend(f"  - {note}" for note in report.notes)

    return "\n".join(lines)


def main() -> int:
    """Console entry point: ``focusguard-hardware``."""
    from focusguard_shared.utilities import configure_logging

    configure_logging(level="INFO", console=False)
    report = detect_hardware()
    print(format_report(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
