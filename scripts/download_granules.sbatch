#!/bin/bash
#SBATCH --job-name=nisar-dl
#SBATCH --output=slurm_%j.out
#SBATCH --error=slurm_%j.out
#SBATCH --time=24:00:00
#SBATCH --cpus-per-task=2
#SBATCH --mem=8G
#
# Downloading is network-bound, not CPU-bound: 2 cores and 8 GB is plenty.
# Walltime is the thing to size. At the ~40 MB/s seen in testing a 7 GB
# granule takes ~3 min, so 38 granules / 261 GB is ~2 h on a good link --
# 24 h is slack for a slow or throttled one. If the job is killed anyway, just resubmit: the
# script resumes every partial file and skips completed ones.
#
#   sbatch download_granules.sbatch
#   tail -f logs.txt

set -euo pipefail

module load miniforge/26.7.2-0
conda activate $HOME/envs/research    # adjust if you put the env elsewhere

cd "$SLURM_SUBMIT_DIR"

# Credentials: prefer a token in the environment over a netrc on shared storage.
# Set it once in your shell before submitting:
#   export EARTHDATA_TOKEN=...          (then sbatch --export=ALL,EARTHDATA_TOKEN ...)
# or keep ~/.netrc at chmod 600.
umask 077

echo "job $SLURM_JOB_ID on $(hostname) starting $(date -Is)"
df -h . | tail -1

python download_granules.py \
    --list granules_to_download.txt \
    --index nisar_granule_index.csv \
    --out-dir ../data/nisar \
    --log logs.txt \
    --progress-every 60 \
    --reserve-gb 20 \
    --dry-run true

echo "job $SLURM_JOB_ID finished $(date -Is) rc=$?"
