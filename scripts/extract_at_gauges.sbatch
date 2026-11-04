#!/bin/bash
#SBATCH --job-name=nisar-extract
#SBATCH --output=../logs/%x-%j.out
#SBATCH --error=../logs/%x-%j.err
#SBATCH --time=02:00:00
#SBATCH --cpus-per-task=2
#SBATCH --mem=16G
#
# Reads a small window per gauge out of each granule, so this is I/O-light
# despite the 261 GB on disk -- minutes, not hours. Memory is for h5py's
# chunk cache, not the arrays.
#
#   mkdir -p ../logs        # SLURM will not create it
#   sbatch extract_at_gauges.sbatch

set -euo pipefail

module load miniforge/26.7.2-0
source "$(conda info --base)/etc/profile.d/conda.sh"
conda activate "$HOME/envs/research"

cd "$SLURM_SUBMIT_DIR"
python -c "import h5py, pyproj, pandas" || { echo "env missing deps"; exit 1; }

python extract_at_gauges.py \
    --granule-dir ../data/nisar \
    --gauges ../data/2026_WaterLevel_Runkle/gauges_2026.csv \
    --out ../outputs/nisar_at_gauges.csv \
    --window 3

echo "done $(date -Is)"
