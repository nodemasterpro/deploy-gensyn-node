#!/bin/bash
# Script de démarrage pour le nœud Gensyn sur RunPod
# Ce script gère la configuration SSH et la détection du type de GPU

set -e

# ==== Configuration SSH ====
echo "🔒 Configuration SSH en cours..."

# Créer le répertoire .ssh avec les permissions appropriées
mkdir -p /root/.ssh
chmod 700 /root/.ssh

# Ajouter la clé SSH depuis la variable d'environnement si disponible
if [ ! -z "$PUBLIC_KEY" ]; then
  echo "$PUBLIC_KEY" > /root/.ssh/authorized_keys
  chmod 600 /root/.ssh/authorized_keys
  echo "Clé SSH ajoutée depuis la variable PUBLIC_KEY"
fi

# Vérifier si RunPod a monté des clés SSH
RUNPOD_SSH_DIR="/runpod-volume/.ssh"
if [ -d "$RUNPOD_SSH_DIR" ] && [ -f "$RUNPOD_SSH_DIR/authorized_keys" ]; then
  cp "$RUNPOD_SSH_DIR/authorized_keys" /root/.ssh/authorized_keys
  chmod 600 /root/.ssh/authorized_keys
  echo "Clés SSH copiées depuis RunPod"
fi

# Vérifier si le service SSH est installé
if ! command -v sshd &> /dev/null; then
    echo "⚠️ Service SSH non installé, installation en cours..."
    apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y openssh-server -qq
fi

# Créer les répertoires nécessaires pour SSH
mkdir -p /run/sshd
mkdir -p /var/run/sshd

# Démarrer le service SSH
echo "🚀 Démarrage du service SSH..."
service ssh start || /usr/sbin/sshd || {
    echo "❌ ERREUR: Impossible de démarrer le service SSH"
    ps aux | grep ssh
}

# Vérifier que SSH est en cours d'exécution
if pgrep -x "sshd" > /dev/null; then
    echo "✅ Service SSH démarré avec succès"
else
    echo "❌ ERREUR: Le service SSH n'a pas démarré"
fi

# ==== Détection du GPU ====
echo "🖥️ Détection du type de GPU..."
WORK_DIR=/root/rl-swarm
CONFIG_PATH="${WORK_DIR}/hivemind_exp/configs/gpu/grpo-qwen-2.5-0.5b-deepseek-r1.yaml"

# Essayer d'obtenir le modèle de GPU en toute sécurité
GPU_MODEL="unknown"
if command -v nvidia-smi &> /dev/null; then
    GPU_MODEL=$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -n 1 || echo "unknown")
    if [ -z "$GPU_MODEL" ]; then
        GPU_MODEL="unknown"
    fi
    echo "GPU détecté: $GPU_MODEL"
else
    echo "nvidia-smi non disponible, impossible de détecter le GPU"
fi

# Sélectionner la configuration appropriée
if [[ "$GPU_MODEL" == *"3090"* ]]; then
    echo "🔹 RTX 3090 détecté, utilisation de la configuration optimisée pour 3090"
    CONFIG_PATH="${WORK_DIR}/hivemind_exp/configs/gpu/grpo-qwen-2.5-0.5b-deepseek-r1-rtx3090.yaml"
    # Mettre à jour le script de lancement
    sed -i "s|--config configs/gpu/grpo-qwen-2.5-0.5b-deepseek-r1.yaml|--config configs/gpu/grpo-qwen-2.5-0.5b-deepseek-r1-rtx3090.yaml|g" ${WORK_DIR}/run_rl_swarm.sh
elif [[ "$GPU_MODEL" == *"4090"* ]]; then
    echo "🔹 RTX 4090 détecté, utilisation de la configuration optimisée pour 4090"
    # La configuration par défaut est déjà optimisée pour RTX 4090
elif [[ "$GPU_MODEL" == *"4080 SUPER"* ]]; then
    echo "🔹 RTX 4080 SUPER détecté, utilisation de la configuration optimisée pour 4090"
    # Utilisation de la configuration 4090 pour 4080 SUPER car ils ont des capacités similaires
else
    echo "🔸 GPU non reconnu ($GPU_MODEL), utilisation de la configuration RTX 3090 par sécurité"
    CONFIG_PATH="${WORK_DIR}/hivemind_exp/configs/gpu/grpo-qwen-2.5-0.5b-deepseek-r1-rtx3090.yaml"
    # Mettre à jour le script de lancement
    sed -i "s|--config configs/gpu/grpo-qwen-2.5-0.5b-deepseek-r1.yaml|--config configs/gpu/grpo-qwen-2.5-0.5b-deepseek-r1-rtx3090.yaml|g" ${WORK_DIR}/run_rl_swarm.sh
fi

echo "✅ Configuration sélectionnée: $CONFIG_PATH"

# ==== Lancement de l'Application ====
echo "🚀 Démarrage de l'application Gensyn..."

if [ -f /root/start.sh ]; then
    exec /root/start.sh
else
    echo "❌ ERREUR: /root/start.sh n'existe pas"
    # Solution de secours: lancer directement le script principal
    cd ${WORK_DIR}
    exec ./run_rl_swarm.sh
fi 