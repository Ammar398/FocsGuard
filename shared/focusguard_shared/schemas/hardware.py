"""Schemas describing detected hardware and the chosen inference provider."""

from __future__ import annotations

from pydantic import BaseModel, Field

from focusguard_shared.constants import GpuVendor, InferenceProvider


class GpuInfo(BaseModel):
    """One physical display adapter, as reported by DXGI."""

    name: str
    vendor: GpuVendor
    vendor_id: int
    device_id: int

    #: Dedicated VRAM in bytes. Taken from DXGI rather than from WMI's
    #: Win32_VideoController.AdapterRAM, which is a 32-bit field and therefore
    #: saturates at 4 GB - it under-reports this machine's 8 GB RX 580.
    dedicated_memory_bytes: int = 0

    is_software_adapter: bool = False

    @property
    def memory_gb(self) -> float:
        return round(self.dedicated_memory_bytes / (1024**3), 2)

    def __str__(self) -> str:
        mem = f"{self.memory_gb:g} GB" if self.dedicated_memory_bytes else "unknown VRAM"
        return f"{self.name} ({mem})"


class CpuInfo(BaseModel):
    name: str
    physical_cores: int = 0
    logical_cores: int = 0
    max_frequency_mhz: float = 0.0

    def __str__(self) -> str:
        return f"{self.name} - {self.physical_cores}C/{self.logical_cores}T"


class HardwareReport(BaseModel):
    """Full startup hardware summary (spec section 46).

    ``notes`` exists for a specific reason: this development machine still has
    nvcuda.dll and nvml.dll present from a GPU that has since been removed. A
    detector that reported "CUDA available" on that basis would be wrong. When
    such a discrepancy is found, the detector records why it was disregarded
    instead of silently ignoring it.
    """

    cpu: CpuInfo
    total_ram_bytes: int = 0
    gpus: list[GpuInfo] = Field(default_factory=list)

    cuda_available: bool = False
    directml_available: bool = False
    available_providers: list[str] = Field(default_factory=list)
    selected_provider: InferenceProvider = InferenceProvider.CPU

    onnxruntime_version: str | None = None
    notes: list[str] = Field(default_factory=list)

    @property
    def total_ram_gb(self) -> float:
        return round(self.total_ram_bytes / (1024**3), 2)

    @property
    def primary_gpu(self) -> GpuInfo | None:
        """The first hardware adapter, ignoring software render adapters."""
        for gpu in self.gpus:
            if not gpu.is_software_adapter:
                return gpu
        return self.gpus[0] if self.gpus else None
