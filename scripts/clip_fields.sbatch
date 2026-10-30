#!/bin/bash
#SBATCH --job-name=nisar-clip
#SBATCH --output=%x-%j.out
#SBATCH --time=01:00:00
#SBATCH --cpus-per-task=1
#SBATCH --mem=8G
#
# Clips every granule to small GeoTIFFs around the 2026 fields. Reads only small
# windows, so it takes minutes; fine to run on a login node instead:
#     python scripts/clip_fields.py
#
#   cd ~/rice-methane-downscaling && sbatch scripts/clip_fields.sbatch

set -euo pipefail
module load miniforge/26.7.2-0
source "$(conda info --base)/etc/profile.d/conda.sh"
conda activate "$HOME/envs/research"        # adjust if your env lives elsewhere

cd "$SLURM_SUBMIT_DIR"
python scripts/clip_fields.py
