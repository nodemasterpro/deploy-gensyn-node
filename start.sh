#!/bin/bash
# Main startup script for Gensyn application

set -e

echo "================================================"
echo "🚀 Starting Gensyn Node"
echo "================================================"

# Environment variables configuration
export PATH="${WORK_DIR}/.venv/bin:$PATH"
export PYTHONPATH="/root/rl-swarm"

# Go to working directory
cd /root/rl-swarm

# Start the service
echo "Starting Gensyn..."
exec ./run_rl_swarm.sh 