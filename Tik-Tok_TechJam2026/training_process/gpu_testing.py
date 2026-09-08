"""Run this on YOUR machine to diagnose GPU/CUDA setup issues with PyTorch.
    python gpu_diagnostic.py
"""
import subprocess
import sys

print("=" * 60)
print("1. NVIDIA driver check (nvidia-smi)")
print("=" * 60)
try:
    r = subprocess.run(["nvidia-smi"], capture_output=True, text=True, timeout=10)
    print(r.stdout if r.returncode == 0 else f"FAILED: {r.stderr}")
except FileNotFoundError:
    print("nvidia-smi not found on PATH — driver may not be installed, "
         "or not added to PATH. This alone would explain CPU-only torch.")

print("\n" + "=" * 60)
print("2. PyTorch build check")
print("=" * 60)
import torch
print(f"torch version:        {torch.__version__}")
print(f"torch built with CUDA: {torch.version.cuda}")   # None = CPU-only wheel
print(f"cuda.is_available():   {torch.cuda.is_available()}")

if torch.version.cuda is None:
    print("\n>>> DIAGNOSIS: this is a CPU-ONLY torch build. torch.version.cuda")
    print(">>> is None, meaning CUDA support was never compiled in. This is")
    print(">>> the single most common cause. Fix: reinstall torch from the")
    print(">>> CUDA-enabled index (command given below).")
elif not torch.cuda.is_available():
    print("\n>>> DIAGNOSIS: torch HAS CUDA support built in, but can't see a")
    print(">>> usable GPU right now. Likely a driver issue, or the driver is")
    print(">>> too old for this torch/CUDA version. Check nvidia-smi output")
    print(">>> above for the driver's max supported CUDA version and compare")
    print(">>> to torch.version.cuda above.")
else:
    print(f"\n>>> torch.cuda.is_available() = True. Device: {torch.cuda.get_device_name(0)}")
    print(">>> Attempting a real forward pass on the GPU to confirm the")
    print(">>> your GPU's compute capability (sm_120, Blackwell) has actual")
    print(">>> compiled kernels in this torch build, not just driver-level detection...")
    try:
        x = torch.randn(4, 4, device="cuda")
        y = x @ x
        torch.cuda.synchronize()
        print(">>> SUCCESS: a real CUDA matmul ran. GPU is fully working.")
        print(">>> If embed.py still printed device=cpu, that's a code-path")
        print(">>> issue, not a hardware/driver one -- tell me and I'll check")
        print(">>> clipfeat.load_clip()'s device selection logic.")
    except RuntimeError as e:
        print(f">>> FAILED with: {e}")
        print(">>> This is the classic 'no kernel image is available for")
        print(">>> execution on the device' error -- torch.cuda.is_available()")
        print(">>> can return True from driver-level detection even when the")
        print(">>> installed torch build has no compiled kernels for the")
        print(">>> this GPU's specific compute capability (sm_120, Blackwell,")
        print(">>> very new). Fix: install a newer torch build (command below).")

print("\n" + "=" * 60)
print("3. Recommended fix if any DIAGNOSIS above fired")
print("=" * 60)
print("Uninstall whatever is there, then install the CUDA 12.4+ build:")
print(f"  {sys.executable} -m pip uninstall -y torch torchvision")
print(f"  {sys.executable} -m pip install torch torchvision "
     "--index-url https://download.pytorch.org/whl/cu124")
print("\nIf that still doesn't detect your GPU (Blackwell needs a genuinely")
print("recent build), try the cu128 index instead:")
print(f"  {sys.executable} -m pip install torch torchvision "
     "--index-url https://download.pytorch.org/whl/cu128")
print("\nAlso worth checking: Settings > this GPU's driver version. Blackwell")
print("cards need a driver from within the last several months -- update via")
print("NVIDIA's site or GeForce Experience/App if nvidia-smi's driver version")
print("looks old.")