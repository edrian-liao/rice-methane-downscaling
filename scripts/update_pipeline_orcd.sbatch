#!/bin/bash
#SBATCH --job-name=nisar-update
#SBATCH --output=logs/update_%j.out
#SBATCH --error=logs/update_%j.out
#SBATCH --time=6:00:00
#SBATCH --cpus-per-task=2
#SBATCH --mem=8G
#
# ORCD half of the refresh: download new granules, clip them, read incidence angles.
# Run `python scripts/update_pipeline.py check` first (laptop or ORCD), then from
# the repo root:
#     mkdir -p logs && sbatch scripts/update_pipeline_orcd.sbatch
#     tail -f logs/download.log
# Resubmitting is safe: finished granules are skipped and partial ones resume.

set -euo pipefail
module load miniforge/26.7.2-0
source "$(conda info --base)/etc/profile.d/conda.sh"
conda activate "$HOME/envs/research"        # adjust if your env lives elsewhere
umask 077                                   # credentials: ~/.netrc (chmod 600) or EARTHDATA_TOKEN

cd "$SLURM_SUBMIT_DIR"
echo "job $SLURM_JOB_ID on $(hostname) starting $(date -Is)"
python scripts/update_pipeline.py orcd
echo "job $SLURM_JOB_ID finished $(date -Is)"
