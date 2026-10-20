#!/usr/bin/env bash
set -euo pipefail
project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_root"
if [[ ! -x third_party/bootstrap_venv/bin/uv ]]; then
    python3 -m venv third_party/bootstrap_venv
    third_party/bootstrap_venv/bin/python -m pip install uv
fi
uv_bin="$project_root/third_party/bootstrap_venv/bin/uv"
export UV_PYTHON_INSTALL_DIR="$project_root/third_party/python"
"$uv_bin" python install 3.11
if [[ ! -x third_party/semantic_venv/bin/python ]]; then
    "$uv_bin" venv --python 3.11 third_party/semantic_venv
fi
"$uv_bin" pip install --python third_party/semantic_venv/bin/python \
    'torch==2.5.1' 'torchvision==0.20.1' 'numpy==1.26.4' \
    pyyaml tqdm psutil wandb plyfile matplotlib seaborn 'opencv-python==4.11.0.86' \
    imageio scipy scikit-learn pandas 'transformers==4.51.0' 'open_clip_torch==2.32.0' \
    'open3d==0.19.0' 'huggingface-hub==0.30.1' 'einops==0.8.1' sentencepiece setuptools wheel
SAM2_BUILD_CUDA=0 "$uv_bin" pip install --python third_party/semantic_venv/bin/python \
    -e third_party/OVO/thirdParty/segment-anything-2 --no-build-isolation
"$uv_bin" pip freeze --python third_party/semantic_venv/bin/python \
    > experiments/multibench_20261020/semantic_requirements.lock
third_party/semantic_venv/bin/python - <<'PY'
import torch
assert torch.cuda.is_available()
x = torch.eye(3, device='cuda')
assert torch.equal(x @ x, x)
print(torch.__version__, torch.version.cuda, torch.cuda.get_device_name(0))
PY
