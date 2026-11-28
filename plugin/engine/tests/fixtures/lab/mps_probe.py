"""Does PyTorch's Metal (MPS) backend work here? Prints ``mps ok`` only after real work ran on the GPU.

Allocates a tensor on ``mps``, multiplies two 512x512 matrices there, waits for the GPU
(``torch.mps.synchronize()``) and checks the result against the CPU. Exits non-zero with a message on stderr
when MPS isn't available or fails; it never falls back to the CPU.
"""

from __future__ import annotations

import sys


def main() -> int:
    try:
        import torch
    except ImportError as exc:
        print(f"mps probe: torch isn't importable: {exc}", file=sys.stderr)
        return 3
    if not torch.backends.mps.is_available():
        built = torch.backends.mps.is_built()
        print(f"mps probe: MPS isn't available (built: {built})", file=sys.stderr)
        return 2
    try:
        a = torch.rand(512, 512, device="mps")
        b = torch.rand(512, 512, device="mps")
        c = a @ b
        torch.mps.synchronize()
        expected = a.cpu() @ b.cpu()
        if c.device.type != "mps" or not torch.allclose(c.cpu(), expected, rtol=1e-3, atol=1e-3):
            print("mps probe: the GPU's result doesn't match the CPU's", file=sys.stderr)
            return 1
    except Exception as exc:  # noqa: BLE001 - any failure on the GPU is the probe's answer
        print(f"mps probe: MPS failed: {exc.__class__.__name__}: {exc}", file=sys.stderr)
        return 1
    print("mps ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
