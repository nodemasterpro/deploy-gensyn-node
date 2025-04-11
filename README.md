# RunPod Manager for Gensyn

A utility to easily deploy and manage Gensyn nodes on RunPod.

## Overview

This utility automates the deployment of Gensyn nodes on RunPod, allowing you to:

- Create/start/stop/terminate GPU pods automatically
- Back up and restore critical Gensyn node identity files
- Manage SSH connections to the pods
- Deploy Gensyn software automatically

The script first attempts to create pods with RTX 4090, then falls back to RTX 3090, and finally to RTX 4080 SUPER if the first two options are unavailable.

## Prerequisites

- Python 3.6+
- RunPod API key
- SSH key pair for connecting to RunPod instances

## Installation

1. Clone this repository
2. Install the required Python dependencies:
```bash
pip install paramiko requests python-dotenv
```

3. Create a `.env` file by copying the template:
```bash
cp .env.template .env
```

4. Edit the `.env` file to add your RunPod API key:
```
RUNPOD_API_KEY=your_api_key_here
```

5. Install the RunPod CLI and generate an SSH key:
```bash
python3 runpod_manager.py install-cli
```

**Important**: You'll need to add the generated public key to your RunPod account in the "SSH Public Keys" section of your account settings.

## Configuration

You can customize the following parameters in the `.env` file:

- `RUNPOD_GPU_TYPE`: GPU type to use (default: NVIDIA GeForce RTX 4090)
- `RUNPOD_DISK_SIZE`: Disk size in GB (default: 30)
- `RUNPOD_TEMPLATE_ID`: Template ID to use (default: jvczrc7se1)
- `RUNPOD_IMAGE`: Docker image to use (default: nodesforall/gensyn-node:latest)
- `RUNPOD_POD_NAME`: Name for the pod (default: gensyn-node)

## Usage Workflows

### Phase 1: First-Time Setup

If you're setting up a Gensyn node for the first time:

1. **Create a new pod**:
   ```bash
   python3 runpod_manager.py create --name "my-gensyn-node"
   ```

2. **Verify the pod is running**:
   ```bash
   python3 runpod_manager.py list
   ```

3. **Connect to the pod via SSH**:
   ```bash
   python3 runpod_manager.py connect
   ```
   Execute the displayed SSH command to connect.

4. **Access the web interface for authentication**:
   Access the web interface at port 3000 to authenticate with Gensyn:
   ```bash
   # While SSH'd into the pod, run:
   echo "Web interface available at: http://$(hostname -I | awk '{print $1}'):3000"
   ```
   Navigate to that URL in your browser and complete the authentication process.

5. **Backup your critical files**:
   After authentication, back up your Gensyn identity files:
   ```bash
   python3 runpod_manager.py backup
   ```
   Or follow the manual backup procedure detailed in the "Manual Backup" section below.

### Phase 2: Account Preservation (Moving to a New Pod)

If you already have a Gensyn node and want to transfer it to a new pod:

1. **Ensure you have backups of your critical files**:
   If you don't have backups already, create them using the backup procedure:
   ```bash
   python3 runpod_manager.py backup
   ```

2. **Terminate the existing pod**:
   ```bash
   python3 runpod_manager.py terminate
   ```

3. **Clean the configuration if needed**:
   ```bash
   python3 runpod_manager.py clean
   ```

4. **Create a new pod**:
   ```bash
   python3 runpod_manager.py create --name "my-gensyn-node"
   ```

5. **Restore your identity files**:
   Follow the manual restore procedure detailed in the "Manual Restore" section below.
   
6. **Verify the node is working correctly**:
   ```bash
   python3 runpod_manager.py connect
   # Once connected, check the logs:
   cd /root/rl-swarm && ./run.sh status
   ```

### Phase 3: Scheduling Pod Operations

To optimize costs, you can schedule automatic start/stop operations:

1. **Add crontab entries to stop the pod during low-activity periods**:
   ```bash
   # Example: Stop the pod at 2am every day
   0 2 * * * cd /path/to/deploy-gensyn-node && python3 runpod_manager.py stop
   ```

2. **Add crontab entries to restart the pod during high-activity periods**:
   ```bash
   # Example: Start the pod at 8am every day
   0 8 * * * cd /path/to/deploy-gensyn-node && python3 runpod_manager.py start
   ```

3. **Monitor pod status regularly**:
   ```bash
   # Example: Check status every 6 hours
   0 */6 * * * cd /path/to/deploy-gensyn-node && python3 runpod_manager.py list > /path/to/pod_status_log.txt
   ```

## Manual Backup of Gensyn Files

To backup critical files from your Gensyn node, follow these steps:

### 1. Connect to the pod

```bash
python3 runpod_manager.py connect
```

### 2. Inside the pod, send the important files

Execute each of the following commands and note the transfer code generated for each file:

```bash
# Send swarm.pem key
runpodctl send /root/rl-swarm/swarm.pem
# Example output: Code is: abcd-1234-efgh

# Send user data
runpodctl send /root/rl-swarm/modal-login/temp-data/userData.json
# Example output: Code is: wxyz-5678-ijkl

# Send API key
runpodctl send /root/rl-swarm/modal-login/temp-data/userApiKey.json
# Example output: Code is: mnop-9012-qrst
```

### 3. Exit the pod

Type `exit` to leave the pod.

### 4. On your local machine, receive the files

Navigate to the folder where you want to save the files:

```bash
# Create and access the backup folder
mkdir -p /root/gensyn/backup
cd /root/gensyn/backup
```

Then execute the receive command for each file, using the transfer codes you noted earlier:

```bash
# Receive swarm.pem
runpodctl receive abcd-1234-efgh

# Receive userData.json
runpodctl receive wxyz-5678-ijkl

# Receive userApiKey.json
runpodctl receive mnop-9012-qrst
```

The files will be saved in the current folder and can be used to restore your node later.

**Important note**: Replace the example codes with the actual codes displayed when sending the files.

## Manual Restore of Gensyn Files

To restore critical files to a new Gensyn node:

### 1. Connect to the pod

```bash
python3 runpod_manager.py connect
```

### 2. Create the necessary directories

Inside the pod, create the directories where the files will be stored:

```bash
mkdir -p /root/rl-swarm/modal-login/temp-data
```

### 3. From your local machine, send the files

In a new terminal on your local machine, navigate to your backup folder and send each file:

```bash
# Go to your backup folder
cd /root/gensyn/backup

# Send the files
runpodctl send swarm.pem
# Note the transfer code: xyz-123-abc

runpodctl send userData.json
# Note the transfer code: def-456-ghi

runpodctl send userApiKey.json
# Note the transfer code: jkl-789-mno
```

### 4. Inside the pod, receive the files

Switch back to your pod SSH session and receive each file in the correct directory:

```bash
# Receive swarm.pem
cd /root/rl-swarm
runpodctl receive xyz-123-abc

# Receive userData.json and userApiKey.json
cd /root/rl-swarm/modal-login/temp-data
runpodctl receive def-456-ghi
runpodctl receive jkl-789-mno
```

### 5. Restart the Gensyn services

```bash
cd /root/rl-swarm
./run.sh restart
```

## Command Reference

### Create a Pod
```bash
python3 runpod_manager.py create --name "my-gensyn-node"
```
This creates a new pod with the specified name. The pod ID is automatically saved in the `.env` file.

### List Existing Pods
```bash
python3 runpod_manager.py list
```

### Start an Existing Pod
```bash
python3 runpod_manager.py start
```
This starts the last created pod.

### Stop a Pod
```bash
python3 runpod_manager.py stop
```
This stops the last created pod.

### Terminate a Pod
```bash
python3 runpod_manager.py terminate
```
This terminates (deletes) the last created pod.

### Get SSH Information
```bash
python3 runpod_manager.py connect
```
This shows SSH connection details for the last created pod.

### Clean Configuration
```bash
python3 runpod_manager.py clean
```
This removes pod information from configuration files to prepare for a fresh pod creation.

## Detailed Command Guide

### Connect to Your Pod
```bash
python3 runpod_manager.py connect
```

The `connect` command:
1. Retrieves the SSH connection information for your pod
2. Verifies that the SSH port is open and accessible
3. Updates the `.env` file with the correct SSH username, host, and port
4. Displays the SSH command you need to execute to connect to your pod

Example output:
```
SSH connection information:
Pod ID: abc123def456
SSH Command: ssh root@38.65.239.24 -p 16251 -i /root/.ssh/id_rsa
```

To connect, just copy and run the displayed SSH command.

### Backup Your Gensyn Files
```bash
python3 runpod_manager.py backup
```

The `backup` command:
1. Checks if your pod exists and is running
2. Creates the local backup directory if it doesn't exist
3. Securely copies these critical files from your pod to the backup directory:
   - `/root/rl-swarm/swarm.pem` (your node identity)
   - `/root/rl-swarm/modal-login/temp-data/userApiKey.json` (authentication)
   - `/root/rl-swarm/modal-login/temp-data/userData.json` (user information)
4. Confirms whether each file was successfully backed up

The backed up files are stored in the `/root/gensyn/backup/` directory.

### Restore Your Gensyn Files
```bash
python3 runpod_manager.py restore
```

The `restore` command:
1. Checks if your pod exists and starts it if it's not running
2. Verifies that all required backup files exist locally
3. Creates the necessary directories on the pod
4. Securely copies the backup files from your local machine to the pod
5. Restarts the Gensyn services on the pod to apply the restored files

This allows you to quickly restore your Gensyn node identity when moving to a new pod.

### Stop Your Pod
```bash
python3 runpod_manager.py stop
```

The `stop` command:
1. Retrieves information about your pod
2. Backs up your Gensyn files automatically before stopping (for safety)
3. Stops the pod using the RunPod API
4. Confirms the pod has been stopped

Stopping a pod:
- Preserves all data on the pod's persistent storage
- Stops billing for the GPU and compute resources
- Continues charging a small fee for storage (disk space)
- Allows you to restart it later without losing your setup

### Start Your Pod
```bash
python3 runpod_manager.py start
```

The `start` command:
1. Checks if your pod exists
2. Starts the pod using the RunPod API
3. Waits for the pod to be fully initialized and SSH-accessible
4. Automatically restores your backed up Gensyn files
5. Restarts the Gensyn services on the pod

This command is ideal for resuming your Gensyn node after it's been stopped, ensuring all your identity files are restored properly.

## Data Persistence

Important data is automatically backed up:

1. On the pod's persistent volume (in `/workspace/gensyn-data`)
2. Locally in the `/root/gensyn/backup/` directory

This data is automatically restored:
- When restarting an existing pod
- When creating a new pod

## Critical Files Backed Up

The most important files for Gensyn identity:

- `swarm.pem`: Gensyn node identity file
- `userApiKey.json`: API key for Gensyn authentication
- `userData.json`: User data for Gensyn

## Costs

The script includes a cost estimation feature with these default rates:
- GPU (RTX 4090): $0.34/hour
- Disk usage: $0.008/GB/hour (applies both when running and stopped)

## Troubleshooting

### SSH Connection Issues

If you experience SSH connection problems:

1. Ensure your SSH public key is added to your RunPod account
2. Wait a few minutes after adding the key for it to propagate
3. Verify the SSH username format is correct (typically `pod_id-hexdigits`)

To test SSH connection manually:
```bash
ssh pod_id-hexdigits@ssh.runpod.io -i ~/.ssh/id_ed25519
```

### API Errors

- Verify your RunPod API key is correct in the `.env` file
- Check if the RunPod API is available and accessible

### Pod Creation Failures

- Check RunPod for available GPU capacity
- Try creating a pod with a different GPU type
- Verify your payment method on RunPod

## Security Notes

- Store your API key securely (never commit the `.env` file to public repositories)
- Use a dedicated SSH key for this application if possible
- The backup directory contains sensitive files; secure it appropriately

## Support

For questions or issues, please create an issue in this repository. 