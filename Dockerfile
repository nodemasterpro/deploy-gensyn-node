FROM ubuntu:22.04

# Avoid interactions during package installation
ENV DEBIAN_FRONTEND=noninteractive

# Environment variables
ENV WORK_DIR=/root/rl-swarm
ENV PATH="${WORK_DIR}/.venv/bin:$PATH"
ENV PIP_FIND_LINKS=https://download.pytorch.org/whl/cu118
ENV NODE_VERSION=18
ENV CONNECT_TO_TESTNET=True
# ENV HF_TOKEN=none  # This line causes an error, we comment it out
ENV ORG_ID=default-org
ENV PYTHONPATH=/root/rl-swarm
ENV PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True,max_split_size_mb:64
# Increase timeout for Hivemind daemon startup
ENV HIVEMIND_DHT_TIMEOUT=60

# Install basic dependencies
RUN apt-get update && apt-get install -y \
    build-essential \
    git \
    python3-venv \
    python3-pip \
    expect \
    curl \
    ca-certificates \
    gnupg \
    openssh-server \
    && mkdir -p /run/sshd /var/run/sshd \
    && echo "PermitRootLogin yes" >> /etc/ssh/sshd_config \
    && echo "AuthorizedKeysFile %h/.ssh/authorized_keys" >> /etc/ssh/sshd_config \
    && rm -rf /var/lib/apt/lists/*

# Install Node.js 18.x
RUN mkdir -p /etc/apt/keyrings && \
    curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key | gpg --dearmor -o /etc/apt/keyrings/nodesource.gpg && \
    echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_${NODE_VERSION}.x nodistro main" | tee /etc/apt/sources.list.d/nodesource.list && \
    apt-get update && \
    apt-get install -y nodejs && \
    npm install -g yarn && \
    rm -rf /var/lib/apt/lists/*

# Clone the repository
WORKDIR /root
RUN git clone -b v0.3.0 https://github.com/gensyn-ai/rl-swarm.git ${WORK_DIR}

# Create logs directory
RUN mkdir -p ${WORK_DIR}/logs

# Configure Python
WORKDIR ${WORK_DIR}
RUN python3 -m venv .venv && \
    . .venv/bin/activate && \
    pip install --upgrade pip setuptools wheel && \
    pip install -r requirements.txt && \
    pip install -r requirements-hivemind.txt && \
    pip install -r requirements_gpu.txt

# Configure the frontend
WORKDIR ${WORK_DIR}/modal-login
RUN rm -rf node_modules yarn.lock && \
    yarn install && \
    yarn add next@latest && \
    yarn add viem@latest

# Return to the working directory
WORKDIR ${WORK_DIR}

# Fix the 'open' command that doesn't exist on Linux
RUN sed -i '67s/open/echo "Interface available at: "/g' ${WORK_DIR}/run_rl_swarm.sh

# Update GPU config file with optimized settings for RTX 4090
RUN echo "# Applying optimized config for RTX 4090" && \
    sed -i '/max_steps:/c\max_steps: 50 # Optimized for RTX 4090' ${WORK_DIR}/hivemind_exp/configs/gpu/grpo-qwen-2.5-0.5b-deepseek-r1.yaml && \
    sed -i '/per_device_train_batch_size:/c\per_device_train_batch_size: 1 # Optimized for RTX 4090 (reduced to prevent OOM)' ${WORK_DIR}/hivemind_exp/configs/gpu/grpo-qwen-2.5-0.5b-deepseek-r1.yaml && \
    sed -i '/gradient_accumulation_steps:/c\gradient_accumulation_steps: 8 # Optimized for RTX 4090' ${WORK_DIR}/hivemind_exp/configs/gpu/grpo-qwen-2.5-0.5b-deepseek-r1.yaml && \
    sed -i '/gradient_checkpointing:/c\gradient_checkpointing: true # Optimized for RTX 4090' ${WORK_DIR}/hivemind_exp/configs/gpu/grpo-qwen-2.5-0.5b-deepseek-r1.yaml && \
    sed -i '/learning_rate:/c\learning_rate: 5.0e-7 # Optimized for RTX 4090' ${WORK_DIR}/hivemind_exp/configs/gpu/grpo-qwen-2.5-0.5b-deepseek-r1.yaml && \
    sed -i '/num_generations:/c\num_generations: 4 # Reduced to prevent OOM' ${WORK_DIR}/hivemind_exp/configs/gpu/grpo-qwen-2.5-0.5b-deepseek-r1.yaml && \
    sed -i '/vllm_gpu_memory_utilization:/c\vllm_gpu_memory_utilization: 0.3 # Reduced to prevent OOM' ${WORK_DIR}/hivemind_exp/configs/gpu/grpo-qwen-2.5-0.5b-deepseek-r1.yaml && \
    sed -i '/max_prompt_length:/c\max_prompt_length: 128 # Reduced to optimize memory usage' ${WORK_DIR}/hivemind_exp/configs/gpu/grpo-qwen-2.5-0.5b-deepseek-r1.yaml && \
    sed -i '/max_completion_length:/c\max_completion_length: 512 # Reduced to optimize memory usage' ${WORK_DIR}/hivemind_exp/configs/gpu/grpo-qwen-2.5-0.5b-deepseek-r1.yaml && \
    if ! grep -q "gradient_checkpointing_kwargs:" ${WORK_DIR}/hivemind_exp/configs/gpu/grpo-qwen-2.5-0.5b-deepseek-r1.yaml; then \
        echo "gradient_checkpointing_kwargs:" >> ${WORK_DIR}/hivemind_exp/configs/gpu/grpo-qwen-2.5-0.5b-deepseek-r1.yaml; \
        echo "  use_reentrant: false" >> ${WORK_DIR}/hivemind_exp/configs/gpu/grpo-qwen-2.5-0.5b-deepseek-r1.yaml; \
        echo "  checkpoint_every_n_layers: 2" >> ${WORK_DIR}/hivemind_exp/configs/gpu/grpo-qwen-2.5-0.5b-deepseek-r1.yaml; \
    fi && \
    if ! grep -q "offload_optimizer:" ${WORK_DIR}/hivemind_exp/configs/gpu/grpo-qwen-2.5-0.5b-deepseek-r1.yaml; then \
        echo "offload_optimizer: true # Enable CPU offloading to save GPU memory" >> ${WORK_DIR}/hivemind_exp/configs/gpu/grpo-qwen-2.5-0.5b-deepseek-r1.yaml; \
    else \
        sed -i '/offload_optimizer:/c\offload_optimizer: true # Enable CPU offloading to save GPU memory' ${WORK_DIR}/hivemind_exp/configs/gpu/grpo-qwen-2.5-0.5b-deepseek-r1.yaml; \
    fi && \
    if ! grep -q "optimize_memory_usage:" ${WORK_DIR}/hivemind_exp/configs/gpu/grpo-qwen-2.5-0.5b-deepseek-r1.yaml; then \
        echo "optimize_memory_usage: true # Enable aggressive memory optimization" >> ${WORK_DIR}/hivemind_exp/configs/gpu/grpo-qwen-2.5-0.5b-deepseek-r1.yaml; \
    else \
        sed -i '/optimize_memory_usage:/c\optimize_memory_usage: true # Enable aggressive memory optimization' ${WORK_DIR}/hivemind_exp/configs/gpu/grpo-qwen-2.5-0.5b-deepseek-r1.yaml; \
    fi && \
    if ! grep -q "bf16:" ${WORK_DIR}/hivemind_exp/configs/gpu/grpo-qwen-2.5-0.5b-deepseek-r1.yaml; then \
        echo "bf16: true # Enable mixed precision training" >> ${WORK_DIR}/hivemind_exp/configs/gpu/grpo-qwen-2.5-0.5b-deepseek-r1.yaml; \
    else \
        sed -i '/bf16:/c\bf16: true # Enable mixed precision training' ${WORK_DIR}/hivemind_exp/configs/gpu/grpo-qwen-2.5-0.5b-deepseek-r1.yaml; \
    fi

# Create a config for RTX 3090 with optimized settings
RUN cp ${WORK_DIR}/hivemind_exp/configs/gpu/grpo-qwen-2.5-0.5b-deepseek-r1.yaml ${WORK_DIR}/hivemind_exp/configs/gpu/grpo-qwen-2.5-0.5b-deepseek-r1-rtx3090.yaml && \
    sed -i '/max_steps:/c\max_steps: 50 # Optimized for RTX 3090' ${WORK_DIR}/hivemind_exp/configs/gpu/grpo-qwen-2.5-0.5b-deepseek-r1-rtx3090.yaml && \
    sed -i '/per_device_train_batch_size:/c\per_device_train_batch_size: 1 # Optimized for RTX 3090' ${WORK_DIR}/hivemind_exp/configs/gpu/grpo-qwen-2.5-0.5b-deepseek-r1-rtx3090.yaml && \
    sed -i '/gradient_accumulation_steps:/c\gradient_accumulation_steps: 16 # Increased for RTX 3090 to compensate for smaller batch size' ${WORK_DIR}/hivemind_exp/configs/gpu/grpo-qwen-2.5-0.5b-deepseek-r1-rtx3090.yaml && \
    sed -i '/learning_rate:/c\learning_rate: 5.0e-7 # Optimized for RTX 3090' ${WORK_DIR}/hivemind_exp/configs/gpu/grpo-qwen-2.5-0.5b-deepseek-r1-rtx3090.yaml && \
    sed -i '/num_generations:/c\num_generations: 2 # Further reduced for RTX 3090 to prevent OOM' ${WORK_DIR}/hivemind_exp/configs/gpu/grpo-qwen-2.5-0.5b-deepseek-r1-rtx3090.yaml && \
    sed -i '/vllm_gpu_memory_utilization:/c\vllm_gpu_memory_utilization: 0.25 # Further reduced for RTX 3090' ${WORK_DIR}/hivemind_exp/configs/gpu/grpo-qwen-2.5-0.5b-deepseek-r1-rtx3090.yaml && \
    sed -i '/max_prompt_length:/c\max_prompt_length: 96 # Reduced even more for RTX 3090' ${WORK_DIR}/hivemind_exp/configs/gpu/grpo-qwen-2.5-0.5b-deepseek-r1-rtx3090.yaml && \
    sed -i '/max_completion_length:/c\max_completion_length: 384 # Reduced even more for RTX 3090' ${WORK_DIR}/hivemind_exp/configs/gpu/grpo-qwen-2.5-0.5b-deepseek-r1-rtx3090.yaml && \
    if ! grep -q "gradient_checkpointing_kwargs:" ${WORK_DIR}/hivemind_exp/configs/gpu/grpo-qwen-2.5-0.5b-deepseek-r1-rtx3090.yaml; then \
        echo "gradient_checkpointing_kwargs:" >> ${WORK_DIR}/hivemind_exp/configs/gpu/grpo-qwen-2.5-0.5b-deepseek-r1-rtx3090.yaml; \
        echo "  use_reentrant: false" >> ${WORK_DIR}/hivemind_exp/configs/gpu/grpo-qwen-2.5-0.5b-deepseek-r1-rtx3090.yaml; \
        echo "  checkpoint_every_n_layers: 1" >> ${WORK_DIR}/hivemind_exp/configs/gpu/grpo-qwen-2.5-0.5b-deepseek-r1-rtx3090.yaml; \
    fi && \
    if ! grep -q "offload_optimizer:" ${WORK_DIR}/hivemind_exp/configs/gpu/grpo-qwen-2.5-0.5b-deepseek-r1-rtx3090.yaml; then \
        echo "offload_optimizer: true # Enable CPU offloading to save GPU memory" >> ${WORK_DIR}/hivemind_exp/configs/gpu/grpo-qwen-2.5-0.5b-deepseek-r1-rtx3090.yaml; \
    else \
        sed -i '/offload_optimizer:/c\offload_optimizer: true # Enable CPU offloading to save GPU memory' ${WORK_DIR}/hivemind_exp/configs/gpu/grpo-qwen-2.5-0.5b-deepseek-r1-rtx3090.yaml; \
    fi && \
    if ! grep -q "optimize_memory_usage:" ${WORK_DIR}/hivemind_exp/configs/gpu/grpo-qwen-2.5-0.5b-deepseek-r1-rtx3090.yaml; then \
        echo "optimize_memory_usage: true # Enable aggressive memory optimization" >> ${WORK_DIR}/hivemind_exp/configs/gpu/grpo-qwen-2.5-0.5b-deepseek-r1-rtx3090.yaml; \
    else \
        sed -i '/optimize_memory_usage:/c\optimize_memory_usage: true # Enable aggressive memory optimization' ${WORK_DIR}/hivemind_exp/configs/gpu/grpo-qwen-2.5-0.5b-deepseek-r1-rtx3090.yaml; \
    fi && \
    if ! grep -q "bf16:" ${WORK_DIR}/hivemind_exp/configs/gpu/grpo-qwen-2.5-0.5b-deepseek-r1-rtx3090.yaml; then \
        echo "bf16: true # Enable mixed precision training" >> ${WORK_DIR}/hivemind_exp/configs/gpu/grpo-qwen-2.5-0.5b-deepseek-r1-rtx3090.yaml; \
    else \
        sed -i '/bf16:/c\bf16: true # Enable mixed precision training' ${WORK_DIR}/hivemind_exp/configs/gpu/grpo-qwen-2.5-0.5b-deepseek-r1-rtx3090.yaml; \
    fi && \
    if ! grep -q "PYTORCH_CUDA_ALLOC_CONF:" ${WORK_DIR}/hivemind_exp/configs/gpu/grpo-qwen-2.5-0.5b-deepseek-r1-rtx3090.yaml; then \
        echo "env_vars:" >> ${WORK_DIR}/hivemind_exp/configs/gpu/grpo-qwen-2.5-0.5b-deepseek-r1-rtx3090.yaml; \
        echo "  PYTORCH_CUDA_ALLOC_CONF: 'expandable_segments:True,max_split_size_mb:64' # Optimize CUDA memory management" >> ${WORK_DIR}/hivemind_exp/configs/gpu/grpo-qwen-2.5-0.5b-deepseek-r1-rtx3090.yaml; \
    fi

# Create GPU detection script to auto-select appropriate config
RUN echo '#!/bin/bash\n\
# Ensure SSH is properly set up\n\
mkdir -p /root/.ssh\n\
chmod 700 /root/.ssh\n\
\n\
# Add SSH key from environment variable if available\n\
if [ ! -z "$PUBLIC_KEY" ]; then\n\
  echo "$PUBLIC_KEY" > /root/.ssh/authorized_keys\n\
  chmod 600 /root/.ssh/authorized_keys\n\
  echo "Added SSH key from PUBLIC_KEY variable"\n\
fi\n\
\n\
# Check if RunPod has mounted SSH keys\n\
RUNPOD_SSH_DIR="/runpod-volume/.ssh"\n\
if [ -d "$RUNPOD_SSH_DIR" ] && [ -f "$RUNPOD_SSH_DIR/authorized_keys" ]; then\n\
  cp "$RUNPOD_SSH_DIR/authorized_keys" /root/.ssh/authorized_keys\n\
  chmod 600 /root/.ssh/authorized_keys\n\
  echo "Copied SSH keys from RunPod"\n\
fi\n\
\n\
# Start SSH service\n\
service ssh start\n\
echo "SSH service started"\n\
\n\
# GPU detection with error handling\n\
CONFIG_PATH="${WORK_DIR}/hivemind_exp/configs/gpu/grpo-qwen-2.5-0.5b-deepseek-r1.yaml"\n\
\n\
# Try to get GPU model safely\n\
GPU_MODEL="unknown"\n\
if command -v nvidia-smi &> /dev/null; then\n\
    GPU_MODEL=$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -n 1 || echo "unknown")\n\
    if [ -z "$GPU_MODEL" ]; then\n\
        GPU_MODEL="unknown"\n\
    fi\n\
    echo "GPU détecté: $GPU_MODEL"\n\
else\n\
    echo "nvidia-smi non disponible, impossible de détecter le GPU"\n\
fi\n\
\n\
if [[ "$GPU_MODEL" == *"3090"* ]]; then\n\
    echo "RTX 3090 détecté, utilisation de la configuration optimisée pour 3090"\n\
    CONFIG_PATH="${WORK_DIR}/hivemind_exp/configs/gpu/grpo-qwen-2.5-0.5b-deepseek-r1-rtx3090.yaml"\n\
    # Ensure the run script uses the RTX 3090 config\n\
    sed -i "s|--config configs/gpu/grpo-qwen-2.5-0.5b-deepseek-r1.yaml|--config configs/gpu/grpo-qwen-2.5-0.5b-deepseek-r1-rtx3090.yaml|g" ${WORK_DIR}/run_rl_swarm.sh\n\
elif [[ "$GPU_MODEL" == *"4090"* ]]; then\n\
    echo "RTX 4090 détecté, utilisation de la configuration optimisée pour 4090"\n\
    # Default config is already optimized for RTX 4090\n\
elif [[ "$GPU_MODEL" == *"4080 SUPER"* ]]; then\n\
    echo "RTX 4080 SUPER détecté, utilisation de la configuration optimisée pour 4090"\n\
    # Using 4090 config for 4080 SUPER as they have similar capabilities\n\
else\n\
    echo "GPU non reconnu ($GPU_MODEL), utilisation de la configuration RTX 3090 par sécurité"\n\
    CONFIG_PATH="${WORK_DIR}/hivemind_exp/configs/gpu/grpo-qwen-2.5-0.5b-deepseek-r1-rtx3090.yaml"\n\
    # Ensure the run script uses the RTX 3090 config\n\
    sed -i "s|--config configs/gpu/grpo-qwen-2.5-0.5b-deepseek-r1.yaml|--config configs/gpu/grpo-qwen-2.5-0.5b-deepseek-r1-rtx3090.yaml|g" ${WORK_DIR}/run_rl_swarm.sh\n\
fi\n\
\n\
echo "Configuration sélectionnée: $CONFIG_PATH"\n\
\n\
# Continue with original startup script\n\
exec /root/start.sh' > /root/gpu_detect.sh && \
    chmod +x /root/gpu_detect.sh

# Copy the startup script
COPY start.sh /root/start.sh
COPY gpu_detect.sh /root/gpu_detect.sh
RUN chmod +x /root/start.sh /root/gpu_detect.sh

# Expose port 3000 for web interface
EXPOSE 3000
# Expose port 22 for SSH access
EXPOSE 22

# Create a directory for persistent data
RUN mkdir -p /workspace/gensyn-data

# Volume for persistent data
VOLUME ["/workspace/gensyn-data"]

# Startup command using our custom script which handles both SSH and GPU detection
CMD ["/root/gpu_detect.sh"]

