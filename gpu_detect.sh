#!/bin/bash
# Startup script for Gensyn node on RunPod
# This script handles SSH configuration and GPU type detection

set -e

# ==== SSH Configuration ====
echo "🔒 Setting up SSH configuration..."

# Create .ssh directory with appropriate permissions
mkdir -p /root/.ssh
chmod 700 /root/.ssh

# Initialize authorized_keys file if it doesn't exist
touch /root/.ssh/authorized_keys
chmod 600 /root/.ssh/authorized_keys

# Add SSH key from environment variable if available (RunPod provides this)
if [ ! -z "$PUBLIC_KEY" ]; then
  # Append key to avoid overwriting existing keys
  echo "$PUBLIC_KEY" >> /root/.ssh/authorized_keys
  echo "✅ SSH key added from PUBLIC_KEY environment variable"
fi

# Check if RunPod has mounted SSH keys
RUNPOD_SSH_DIR="/runpod-volume/.ssh"
if [ -d "$RUNPOD_SSH_DIR" ] && [ -f "$RUNPOD_SSH_DIR/authorized_keys" ]; then
  # Append keys to avoid overwriting
  cat "$RUNPOD_SSH_DIR/authorized_keys" >> /root/.ssh/authorized_keys
  echo "✅ SSH keys copied from RunPod volume"
fi

# Remove duplicate keys if any
sort -u /root/.ssh/authorized_keys > /root/.ssh/authorized_keys.tmp
mv /root/.ssh/authorized_keys.tmp /root/.ssh/authorized_keys
chmod 600 /root/.ssh/authorized_keys

# Verify SSH key was added
if [ -s /root/.ssh/authorized_keys ]; then
  echo "✅ SSH keys configured successfully"
  echo "Keys in authorized_keys:"
  cat /root/.ssh/authorized_keys | while read line; do
    echo "- ${line:0:40}..."
  done
else
  echo "⚠️ WARNING: No SSH keys found in authorized_keys file"
fi

# Check if SSH server is installed
if ! command -v sshd &> /dev/null; then
    echo "⚠️ SSH server not installed, installing now..."
    apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y openssh-server -qq
fi

# Install net-tools for netstat if missing
if ! command -v netstat &> /dev/null; then
    echo "⚠️ netstat not found, installing net-tools..."
    apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y net-tools -qq
fi

# Create necessary directories for SSH
mkdir -p /run/sshd
mkdir -p /var/run/sshd

# Configure SSH server for better compatibility
if [ -f /etc/ssh/sshd_config ]; then
  # Make backup of original config
  cp /etc/ssh/sshd_config /etc/ssh/sshd_config.bak

  # Allow root login
  sed -i 's/#PermitRootLogin prohibit-password/PermitRootLogin yes/' /etc/ssh/sshd_config
  # Make sure root login is enabled (another possible format)
  sed -i 's/PermitRootLogin without-password/PermitRootLogin yes/' /etc/ssh/sshd_config
  # Enable public key authentication
  sed -i 's/#PubkeyAuthentication yes/PubkeyAuthentication yes/' /etc/ssh/sshd_config
  # Ensure TCP forwarding is enabled (for tunnels)
  sed -i 's/#AllowTcpForwarding yes/AllowTcpForwarding yes/' /etc/ssh/sshd_config
  # Disable password authentication for security
  sed -i 's/#PasswordAuthentication yes/PasswordAuthentication no/' /etc/ssh/sshd_config
  
  echo "✅ SSH server configured for RunPod compatibility"
fi

# Start SSH service
echo "🚀 Starting SSH service..."
service ssh restart || systemctl restart ssh || /usr/sbin/sshd || {
    echo "❌ ERROR: Unable to start SSH service"
    ps aux | grep ssh
}

# Verify SSH is running
if pgrep -x "sshd" > /dev/null; then
    echo "✅ SSH service started successfully"
    # Show SSH service status if netstat is available
    if command -v netstat &> /dev/null; then
        echo "SSH listening ports:"
        netstat -tulpn | grep ssh
    else
        echo "SSH process is running (netstat not available to show ports)"
        ps aux | grep sshd | grep -v grep
    fi

    # Show SSH daemon status
    if command -v systemctl &> /dev/null; then
        systemctl status ssh || echo "systemctl status command failed"
    elif command -v service &> /dev/null; then
        service ssh status || echo "service status command failed"
    fi
else
    echo "❌ ERROR: SSH service did not start"
fi

# Try to diagnose any SSH issues
echo "Testing SSH connectivity..."
if command -v ss &> /dev/null; then
    echo "SSH listening sockets:"
    ss -tulpn | grep ssh
elif command -v lsof &> /dev/null; then
    echo "SSH open files:"
    lsof -i:22
fi

# Install iproute2 if needed for 'ip' command
if ! command -v ip &> /dev/null; then
    echo "⚠️ ip command not found, installing iproute2..."
    apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y iproute2 -qq
fi

# Display public IP if available
echo "IP addresses on this machine:"
if command -v ip &> /dev/null; then
    ip addr show
elif command -v ifconfig &> /dev/null; then
    ifconfig
else
    hostname -I || echo "No tool available to show IP addresses"
fi

# Verify SSH direct connectivity
echo "Verifying SSH direct connectivity (important for SCP/SFTP)..."
# Check if port 22 is accessible from outside
if command -v curl &> /dev/null; then
    echo "Testing if port 22 is exposed to outside..."
    PUBLIC_IP=$(curl -s https://api.ipify.org || curl -s http://checkip.amazonaws.com || echo "unknown")
    if [ "$PUBLIC_IP" != "unknown" ]; then
        echo "Public IP appears to be: $PUBLIC_IP"
        echo "To connect via SSH, try: ssh root@$PUBLIC_IP -i ~/.ssh/id_ed25519"
    else
        echo "Could not determine public IP"
    fi
fi

# Check if RunPod assigned a public IP with port mapping
if [ ! -z "$RUNPOD_TCP_PORT_22" ]; then
    echo "RunPod TCP port mapping for SSH: $RUNPOD_TCP_PORT_22"
    echo "This indicates SSH should be accessible via TCP with the port mapping"
    echo "To connect: ssh root@<runpod-ip> -p $RUNPOD_TCP_PORT_22 -i ~/.ssh/id_ed25519"
else
    echo "No RunPod TCP port mapping found for SSH"
    echo "You may need to use the tunnel connection provided by RunPod"
fi

# ==== GPU Detection ====
echo "🖥️ Detecting GPU type..."
WORK_DIR=/root/rl-swarm
CONFIG_PATH="${WORK_DIR}/hivemind_exp/configs/gpu/grpo-qwen-2.5-0.5b-deepseek-r1.yaml"

# Try to get GPU model safely
GPU_MODEL="unknown"
if command -v nvidia-smi &> /dev/null; then
    GPU_MODEL=$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -n 1 || echo "unknown")
    if [ -z "$GPU_MODEL" ]; then
        GPU_MODEL="unknown"
    fi
    echo "Detected GPU: $GPU_MODEL"
else
    echo "nvidia-smi not available, cannot detect GPU"
fi

# Select appropriate configuration
if [[ "$GPU_MODEL" == *"3090"* ]]; then
    echo "🔹 RTX 3090 detected, using optimized configuration for 3090"
    CONFIG_PATH="${WORK_DIR}/hivemind_exp/configs/gpu/grpo-qwen-2.5-0.5b-deepseek-r1-rtx3090.yaml"
    # Update launch script
    sed -i "s|--config configs/gpu/grpo-qwen-2.5-0.5b-deepseek-r1.yaml|--config configs/gpu/grpo-qwen-2.5-0.5b-deepseek-r1-rtx3090.yaml|g" ${WORK_DIR}/run_rl_swarm.sh
elif [[ "$GPU_MODEL" == *"4090"* ]]; then
    echo "🔹 RTX 4090 detected, using optimized configuration for 4090"
    # Default configuration is already optimized for RTX 4090
elif [[ "$GPU_MODEL" == *"4080 SUPER"* ]]; then
    echo "🔹 RTX 4080 SUPER detected, using optimized configuration for 4090"
    # Using 4090 configuration for 4080 SUPER as they have similar capabilities
else
    echo "🔸 Unrecognized GPU ($GPU_MODEL), using RTX 3090 configuration for safety"
    CONFIG_PATH="${WORK_DIR}/hivemind_exp/configs/gpu/grpo-qwen-2.5-0.5b-deepseek-r1-rtx3090.yaml"
    # Update launch script
    sed -i "s|--config configs/gpu/grpo-qwen-2.5-0.5b-deepseek-r1.yaml|--config configs/gpu/grpo-qwen-2.5-0.5b-deepseek-r1-rtx3090.yaml|g" ${WORK_DIR}/run_rl_swarm.sh
fi

echo "✅ Selected configuration: $CONFIG_PATH"

# ==== Application Launch ====
echo "🚀 Starting Gensyn application..."

if [ -f /root/start.sh ]; then
    exec /root/start.sh
else
    echo "❌ ERROR: /root/start.sh doesn't exist"
    # Fallback: launch the main script directly
    cd ${WORK_DIR}
    exec ./run_rl_swarm.sh
fi 