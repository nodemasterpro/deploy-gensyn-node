#!/bin/bash

# Gensyn Services Restart Script
echo "==== Restarting Gensyn Services ===="

# Restart Docker container if it exists
if command -v docker &> /dev/null; then
  echo "Docker found, restarting container..."
  docker restart gensyn-node || echo "Gensyn-node container not found"
else
  echo "Docker not installed, skipping container restart"
fi

# Stop existing processes
echo "Stopping hivemind processes..."
pkill -f hivemind || echo "No hivemind process found"

echo "Stopping swarm processes..."
pkill -f swarm || echo "No swarm process found"

# Clean shared memory directory
echo "Cleaning shared memory directory..."
rm -rf /dev/shm/* || echo "Could not clean /dev/shm/"

# Start Gensyn service if the script exists
if [ -f /root/rl-swarm/run_swarm.sh ]; then
  echo "Starting Gensyn service..."
  cd /root/rl-swarm && bash run_swarm.sh &
  echo "Gensyn service started in background"
else
  echo "Gensyn start script not found at /root/rl-swarm/run_swarm.sh"
fi

echo "==== Gensyn Services Restart Complete ====" 