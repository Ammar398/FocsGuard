"""Export YOLOv8n to ONNX. Developer tool - run once, never at runtime.

    python scripts/export_yolo.py

WHY THIS IS A SEPARATE SCRIPT AND NOT A DEPENDENCY
--------------------------------------------------
`ultralytics` pulls in PyTorch. On this machine that buys nothing at runtime:

* the GPU is an **AMD Radeon RX 580**, so PyTorch on Windows offers only its CPU
  path - the heaviest dependency in the project would run YOLO on the same four
  threads MediaPipe needs;
* ONNX Runtime with the DirectML provider *does* use the RX 580, moving phone
  detection off the CPU entirely.

So PyTorch is used exactly once, here, to produce a ~12 MB ONNX file. The agent
then loads that file through `onnxruntime-directml` and never imports torch.

RUN IT IN A THROWAWAY ENVIRONMENT
---------------------------------
`ultralytics` depends on `opencv-python`, which installs the same `cv2` module as
the `opencv-contrib-python` the agent uses. Installing it into the main venv risks
replacing a working OpenCV. `scripts/export_yolo.ps1` builds a separate venv,
runs this, and deletes it.

WHY CLASS 67
------------
The model is COCO-pretrained and class 67 is `cell phone`, which is exactly what
spec section 16 needs. No training or dataset is required.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

# COCO class index for a mobile phone.
CELL_PHONE_CLASS_ID = 67


def export(model_name: str, image_size: int, output_dir: Path) -> int:
    try:
        from ultralytics import YOLO
    except ImportError:
        print("ultralytics is not installed in this interpreter.", file=sys.stderr)
        print("Run scripts/export_yolo.ps1, which builds a throwaway venv.", file=sys.stderr)
        return 1

    print(f"Loading {model_name} (downloads the weights on first run) ...")
    model = YOLO(model_name)

    names = model.names or {}
    detected = names.get(CELL_PHONE_CLASS_ID)
    if detected != "cell phone":
        # Guard against a future model with a different class order silently
        # making the agent detect the wrong object.
        print(
            f"WARNING: class {CELL_PHONE_CLASS_ID} is {detected!r}, expected 'cell phone'.",
            file=sys.stderr,
        )

    print(f"Exporting to ONNX at {image_size}x{image_size} ...")
    # A fixed input size lets DirectML pick one kernel configuration and keep it,
    # which is measurably faster than a dynamic shape for a stream of same-sized
    # frames. simplify=False avoids a further dependency on onnxsim.
    exported = model.export(
        format="onnx",
        imgsz=image_size,
        opset=12,
        dynamic=False,
        simplify=False,
    )

    source = Path(exported)
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / "yolov8n.onnx"
    shutil.copy2(source, target)

    size_mb = target.stat().st_size / 1e6
    print(f"\nExported to {target} ({size_mb:.1f} MB)")
    print(f"Class {CELL_PHONE_CLASS_ID} = cell phone")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Export YOLOv8n to ONNX (developer tool)")
    parser.add_argument("--model", default="yolov8n.pt", help="source weights")
    parser.add_argument("--imgsz", type=int, default=320, help="square input size")
    parser.add_argument("--out", type=Path, default=None, help="output directory")
    args = parser.parse_args()

    if args.out is None:
        # Default to the agent's model cache, outside the repository.
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "shared"))
        from focusguard_shared.utilities.paths import model_cache_dir

        args.out = model_cache_dir()

    return export(args.model, args.imgsz, args.out)


if __name__ == "__main__":
    raise SystemExit(main())
