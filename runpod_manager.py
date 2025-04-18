#!/usr/bin/env python3
"""
RunPod Manager - Automated GPU instance management for Gensyn nodes
"""

import os
import sys
import time
import json
import argparse
import subprocess
import requests
import paramiko
import dotenv
from datetime import datetime
import re
import socket
import shutil
import traceback

# Load environment variables from .env file
dotenv.load_dotenv()

# Configuration (loaded from environment variables)
API_KEY = os.getenv("RUNPOD_API_KEY", "")
# Modification: first initialize with default value, then replace if .env contains a value
SSH_KEY_PATH = "~/.ssh/id_rsa"  # Default value
env_ssh_key = os.getenv("SSH_KEY_PATH")
if env_ssh_key:
    SSH_KEY_PATH = env_ssh_key
SSH_KEY_PATH = os.path.expanduser(SSH_KEY_PATH)  # Expand path in all cases
RUNPOD_API_URL = "https://api.runpod.io/graphql"
# SSH username variable (empty by default, will be updated when creating the pod)
SSH_USERNAME = os.getenv("SSH_USERNAME", "")
SSH_HOST = os.getenv("SSH_HOST", "ssh.runpod.io")
SSH_PORT = int(os.getenv("SSH_PORT", "22"))

# Default environment variables
DEFAULT_GPU_TYPE = os.getenv("RUNPOD_GPU_TYPE", "NVIDIA GeForce RTX 4090")
DEFAULT_DISK_SIZE = int(os.getenv("RUNPOD_DISK_SIZE", "30"))
DEFAULT_TEMPLATE_ID = os.getenv("RUNPOD_TEMPLATE_ID", "jvczrc7se1")
DEFAULT_IMAGE = os.getenv("RUNPOD_IMAGE", "nodesforall/gensyn-node:latest")
DEFAULT_POD_NAME = os.getenv("RUNPOD_POD_NAME", "gensyn-node")

# Default pod configuration - simplified for template creation
DEFAULT_POD_CONFIG = {
    'name': DEFAULT_POD_NAME,  # Use environment variable for pod name
    'gpu': DEFAULT_GPU_TYPE,  # Use environment variable
    'templateId': DEFAULT_TEMPLATE_ID,  # Use environment variable
    'image': DEFAULT_IMAGE,  # Use environment variable
    'containerDiskInGb': DEFAULT_DISK_SIZE,  # Use correct size of 30 GB
    'diskInGb': DEFAULT_DISK_SIZE,  # Add here as well
    'dockerArgs': '--volume gensyn-data:/workspace/gensyn-data'
}

# Define the Gensyn backup directory - Always use /root/gensyn/backup for consistency
GENSYN_BACKUP_DIR = "/root/gensyn/backup"
# Create the backup directory if it doesn't exist
os.makedirs(GENSYN_BACKUP_DIR, exist_ok=True)

def ensure_ssh_key_exists():
    """Ensure that the SSH key exists, generating it if necessary"""
    ssh_key_path = get_ssh_key_path()
    if not os.path.exists(ssh_key_path):
        print(f"SSH key {ssh_key_path} doesn't exist. Generating...")
        
        # Determine the type of key to generate
        key_type = "ed25519" if "ed25519" in ssh_key_path else "rsa"
        bits = "" if key_type == "ed25519" else "-b 4096"
        
        # Generate the key without passphrase
        cmd = f"ssh-keygen -t {key_type} {bits} -f {ssh_key_path} -N ''"
        print(f"Executing command: {cmd}")
        
        try:
            subprocess.run(cmd, shell=True, check=True)
            print(f"SSH key {key_type} generated successfully")
            
            # Display the public key so the user can add it to RunPod
            if os.path.exists(f"{ssh_key_path}.pub"):
                with open(f"{ssh_key_path}.pub", "r") as f:
                    public_key = f.read().strip()
                print("\n\nIMPORTANT: Add this public key to your RunPod account:\n")
                print(f"{public_key}\n")
                print("Add it at: https://runpod.io/console/user/settings in the 'SSH Public Keys' section")
                print("Wait a few minutes for the key to propagate before attempting an SSH connection\n")
            
            return True
        except subprocess.CalledProcessError as e:
            print(f"Error generating SSH key: {e}")
            return False
    else:
        print(f"SSH key {ssh_key_path} already exists")
        return True

class RunPodManager:
    """
    Class to manage RunPod GPU instances for Gensyn nodes.
    """
    
    def __init__(self, api_key):
        """Initialize with API key"""
        self.api_key = api_key
        self.headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}"
        }
        os.makedirs(GENSYN_BACKUP_DIR, exist_ok=True)
        
    def get_api_schema(self):
        """Fetch API schema to understand the correct payload structure"""
        try:
            response = requests.get(
                f"{RUNPOD_API_URL}/openapi.json",
                headers=self.headers
            )
            print(f"API Schema Response: {response.status_code}")
            if response.status_code == 200:
                schema = response.json()
                # Try to find the pod creation schema
                if 'paths' in schema and '/pods' in schema['paths']:
                    print("Found pod creation schema!")
                    pod_post_schema = schema['paths']['/pods'].get('post', {})
                    print(json.dumps(pod_post_schema, indent=2))
                return True
            return False
        except Exception as e:
            print(f"Error fetching API schema: {e}")
            return False
        
    def get_pod_cost(self, gpu_id="NVIDIA GeForce RTX 4090", disk_gb=20):
        """Calculate approximate pod cost per hour"""
        # These are simplified calculations based on the rates provided
        gpu_cost_per_hour = 0.34  # $0.34/h for GPU
        disk_cost_per_hour = disk_gb * 0.008 / 100  # $0.008/h per GB
        return {
            "running_cost_per_hour": gpu_cost_per_hour + disk_cost_per_hour,
            "stopped_cost_per_hour": disk_cost_per_hour,
            "gpu_cost": gpu_cost_per_hour,
            "disk_cost": disk_cost_per_hour
        }

    def create_pod(self, config=None):
        """Create a pod using the RunPod CLI, trying first RTX 4090 then falling back to RTX 3090 or RTX 4080 SUPER if not available"""
        if config is None:
            config = DEFAULT_POD_CONFIG.copy()
        original_gpu = config['gpu']
        
        # First try with RTX 4090
        print(f"Attempting to create a pod with {config['gpu']}...")
        pod_id = self.create_pod_cli(config)
        
        # If RTX 4090 failed, try with RTX 3090
        if not pod_id and original_gpu == "NVIDIA GeForce RTX 4090":
            print("RTX 4090 not available. Trying with RTX 3090...")
            config['gpu'] = "NVIDIA GeForce RTX 3090"
            pod_id = self.create_pod_cli(config)
            
            # If RTX 3090 also failed, try with RTX 4080 SUPER
            if not pod_id:
                print("RTX 3090 not available. Trying with RTX 4080 SUPER...")
                config['gpu'] = "NVIDIA GeForce RTX 4080 SUPER"
                pod_id = self.create_pod_cli(config)
                
                if pod_id:
                    print(f"Pod successfully created with RTX 4080 SUPER! ID: {pod_id}")
            elif pod_id:
                print(f"Pod successfully created with RTX 3090! ID: {pod_id}")
        elif pod_id:
            print(f"Pod successfully created with {config['gpu']}! ID: {pod_id}")
        
        return pod_id

    def get_pod_status_cli(self, pod_id):
        """Get pod status using CLI command"""
        print(f"Getting status for pod {pod_id} via CLI...")
        try:
            # Use 'runpodctl get pod ID' without the --output option
            result = subprocess.run(
                ["runpodctl", "get", "pod", pod_id], 
                capture_output=True, 
                text=True, 
                check=True
            )
            
            # Parse the output to extract the status
            output = result.stdout.strip()
            print(f"CLI Output: {output}")
            
            # Check if information is present in the output
            if pod_id in output:
                # Initialize with a default status
                status = "UNKNOWN"
                
                # Look for precise status indicators
                if "EXITED" in output:
                    status = "EXITED"
                elif "STOPPING" in output:
                    status = "STOPPING"
                elif "STARTING" in output:
                    status = "STARTING"
                elif "TERMINATED" in output:
                    status = "TERMINATED"
                elif "STOPPED" in output:
                    status = "STOPPED"
                elif "RUNNING" in output:
                    status = "RUNNING"
                
                # Convert stdout to a dict
                pod_data = {"id": pod_id}
                
                if pod_id in output:
                    # Attempt to parse information from the output
                    lines = output.strip().split('\n')
                    if len(lines) >= 2:
                        headers = lines[0].split()
                        values = lines[1].split()
                        if len(headers) == len(values):
                            for i, header in enumerate(headers):
                                pod_data[header.lower()] = values[i]
                
                return status, pod_data
            else:
                print(f"Error: Pod {pod_id} not found in output")
                return "NOT_FOUND", None
            
        except subprocess.CalledProcessError as e:
            print(f"CLI command failed: {e}")
            print(f"Error output: {e.stderr}")
            if "not found" in str(e.stderr) or "does not exist" in str(e.stderr) or "Resource does not exist" in str(e.stderr):
                print(f"Error: Pod {pod_id} not found")
                return "NOT_FOUND", None
            return "ERROR", None

    def get_pod_status(self, pod_id):
        """Get the status of a pod (CLI version)"""
        # Prefer the CLI version
        return self.get_pod_status_cli(pod_id)
        
    def get_pod_status_api(self, pod_id):
        """Get the status of a pod using API"""
        try:
            response = requests.get(
                f"{RUNPOD_API_URL}/pods/{pod_id}",
                headers=self.headers
            )
            print(f"Status API Response: {response.status_code}")
            print(f"Status Response Content: {response.text}")
            
            response.raise_for_status()
            
            # Check if the response is a dictionary - API v1 returns the pod directly
            pod_data = response.json()
            if isinstance(pod_data, dict):
                status = pod_data.get("desiredStatus", pod_data.get("status", "UNKNOWN"))
                print(f"Pod status: {status}")
                
                # Save pod info for future reference if it's running
                if status == "RUNNING":
                    with open(f"{GENSYN_BACKUP_DIR}/pod_info.json", "w") as f:
                        json.dump(pod_data, f, indent=2)
                        
                return status, pod_data
                
            return "UNKNOWN", None
                
        except requests.RequestException as e:
            print(f"API Request error: {e}")
            return "ERROR", None

    def wait_for_pod_ready(self, pod_id, timeout=600):
        """Wait for pod to be in 'running' state"""
        print(f"Waiting for pod {pod_id} to be ready...")
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            status, pod_data = self.get_pod_status(pod_id)
            
            # Check for READY or RUNNING status according to API v1
            if status == "RUNNING" or status == "READY":
                print(f"Pod {pod_id} is now running!")
                return pod_data
            
            # Check for terminal states
            if status in ["EXITED", "TERMINATED", "FAILED", "OUT_OF_CREDIT"]:
                print(f"Pod {pod_id} failed to start: {status}")
                return None
                
            print(f"Current status: {status}. Waiting...")
            time.sleep(15)
            
        print(f"Timed out waiting for pod {pod_id} to be ready")
        return None

    def start_pod_cli(self, pod_id, manual_ssh_port=None, manual_ssh_host=None):
        """Start a pod using CLI command and restore critical files

        This function:
        1. Starts the pod using runpodctl
        2. Waits for the pod to be ready
        3. Uses runpodctl connect to get the correct SSH information
        4. Waits for SSH to be available 
        5. Restores critical Gensyn files from backup
        """
        print(f"Starting pod {pod_id} via CLI...")
        try:
            # First start the pod with runpodctl
            result = subprocess.run(
                ["runpodctl", "start", "pod", pod_id], 
                capture_output=True, 
                text=True, 
                check=True
            )
            print(result.stdout)
            
            # Wait for pod to be ready
            print(f"Waiting for pod {pod_id} to be ready...")
            pod_data = self.wait_for_pod_ready(pod_id, timeout=300)
            
            if not pod_data:
                print("Pod failed to start or timed out. Cannot restore Gensyn files.")
                return False
                
            print("Pod is ready. Getting SSH information...")
            
            # Use the improved method to get SSH information
            ssh_info = self.get_updated_ssh_info(pod_id, max_attempts=20, delay=30)
            
            # If we couldn't get the SSH information, we can't continue
            if not ssh_info:
                print("❌ Unable to obtain the necessary SSH information.")
                print("Please manually run the command 'python3 runpod_manager.py connect' to update connection information,")
                print("then run 'python3 runpod_manager.py restore' to restore the files.")
                return False
                
            # Add SSH information to pod_data for restore_gensyn
            pod_data["ssh_user"] = ssh_info.get("ssh_user")
            pod_data["ssh_host"] = ssh_info.get("ssh_host")
            pod_data["ssh_port"] = ssh_info.get("ssh_port")
            pod_data["ssh_key_path"] = ssh_info.get("ssh_key_path")
                
            # Restore Gensyn files
            print("Restoring Gensyn files from backup...")
            restore_result = self.restore_gensyn(pod_data)
            
            if restore_result:
                print("✅ Gensyn files restored successfully!")
            else:
                print("⚠️ Could not restore Gensyn files. The pod may need manual configuration.")
                print("Try running 'python3 runpod_manager.py restore' manually.")
                
            return True
            
        except subprocess.CalledProcessError as e:
            print(f"CLI command failed: {e}")
            print(f"Error output: {e.stderr}")
            return False

    def stop_pod_cli(self, pod_id):
        """Stop a pod with backup of critical files beforehand
        
        This function:
        1. Gets pod data
        2. Backs up critical Gensyn files
        3. Stops the pod using runpodctl
        """
        print(f"Stopping pod {pod_id} via CLI...")
        try:
            # First get pod data for backup
            status, pod_data = self.get_pod_status(pod_id)
            
            if status in ["RUNNING", "READY"]:
                # Backup critical files before stopping
                print("Backing up critical Gensyn files before stopping the pod...")
                backup_result = self.backup_gensyn_data(pod_data)
                
                if backup_result:
                    print("✅ Critical files backed up successfully!")
                else:
                    print("⚠️ WARNING: Failed to backup critical files.")
                    user_input = input("Continue with pod stop anyway? (y/n): ")
                    if user_input.lower() != 'y':
                        print("Operation cancelled by user")
                        return False
            else:
                print(f"Pod is in status {status}, cannot backup files")
            
            # Stop the pod
            result = subprocess.run(
                ["runpodctl", "stop", "pod", pod_id], 
                capture_output=True, 
                text=True, 
                check=True
            )
            print(result.stdout)
            return True
            
        except subprocess.CalledProcessError as e:
            print(f"CLI command failed: {e}")
            print(f"Error output: {e.stderr}")
            return False
                
    def terminate_pod_cli(self, pod_id):
        """Terminate a pod using CLI command"""
        print(f"Terminating pod {pod_id} via CLI...")
        try:
            # The correct command is 'runpodctl remove pod'
            result = subprocess.run(
                ["runpodctl", "remove", "pod", pod_id], 
                capture_output=True, 
                text=True, 
                check=True
            )
            print(result.stdout)
            return True
        except subprocess.CalledProcessError as e:
            print(f"CLI command failed: {e}")
            print(f"Error output: {e.stderr}")
            return False

    def list_pods_cli(self):
        """List pods using CLI command"""
        print("Listing pods via CLI...")
        try:
            # First check if the CLI is correctly configured
            try:
                # Explicitly ask for all pods
                result = subprocess.run(
                    ["runpodctl", "get", "pods"], 
                    capture_output=True, 
                    text=True, 
                    check=True
                )
                output = result.stdout.strip()
                print(output)
                
                # If we see "You have no pods", it means the CLI is configured but no pod exists
                if "no pods" in output.lower():
                    print("You have no active pods")
                    return []
                
                # If we have a pod saved in .env, let's try to use it
                saved_pod_id = load_pod_id()
                if saved_pod_id:
                    print(f"Attempt to retrieve information for pod ID: {saved_pod_id}")
                    status, pod_data = self.get_pod_status_cli(saved_pod_id)
                    
                    # Get detailed information with -a flag
                    detailed_result = subprocess.run(
                        ["runpodctl", "get", "pod", saved_pod_id, "-a"], 
                        capture_output=True, 
                        text=True
                    )
                    
                    if detailed_result.returncode == 0:
                        # Analyze output to extract SSH and HTTP info
                        pod_output = detailed_result.stdout
                        
                        # Build the SSH URL in the correct format (which works more reliably)
                        # Format: pod-id-hexcode@ssh.runpod.io
                        # First try to find the hexadecimal code dynamically
                        hex_suffix = "64411701"  # Default value provided by user
                        
                        # Search for specific format in the output
                        ssh_pattern = re.search(rf'{saved_pod_id}-([a-f0-9]+)@ssh\.runpod\.io', pod_output)
                        if ssh_pattern:
                            hex_suffix = ssh_pattern.group(1)
                            print(f"Discovered hexadecimal suffix: {hex_suffix}")
                        
                        pod_ssh_user = f"{saved_pod_id}-{hex_suffix}"
                        ssh_host = "ssh.runpod.io"
                        print(f"\n🔑 SSH Connection: ssh {pod_ssh_user}@{ssh_host} -i ~/.ssh/id_ed25519")
                        
                        # Save these details to .env for later use
                        save_pod_id_env(saved_pod_id, pod_ssh_user, ssh_host, 22)
                        
                        pod_data = {
                            "id": saved_pod_id,
                            "status": status,
                            "name": pod_data.get("name", "UNKNOWN"),
                            "gpu": pod_data.get("gpu", "UNKNOWN"),
                            "sshUrl": pod_data.get("sshUrl", "UNKNOWN"),
                            "costPerHr": pod_data.get("costPerHr", "N/A")
                        }
                        
                        return [pod_data]
                
                # Check if we have a valid list of pods (at least one ID)
                lines = output.split('\n')
                pods = []
                
                # If we have at least 2 lines (header + at least 1 pod)
                if len(lines) >= 2:
                    # Expected format in output lines: ID NAME GPU IMAGE STATUS
                    for i, line in enumerate(lines):
                        # Ignore the header line
                        if i == 0 or not line.strip():
                            continue
                        
                        # Analyze the line correctly
                        parts = line.strip().split()
                        if len(parts) >= 1:
                            pod_id = parts[0]
                            # Check if it's a valid ID (at least 12 characters and not starting with hyphen)
                            if len(pod_id) >= 12 and not pod_id.startswith('-'):
                                print(f"Pod detected with ID: {pod_id}")
                                # Get pod details
                                status, pod_data = self.get_pod_status_cli(pod_id)
                                
                                # Get detailed information for connection strings
                                detailed_result = subprocess.run(
                                    ["runpodctl", "get", "pod", pod_id, "-a"], 
                                    capture_output=True, 
                                    text=True
                                )
                                
                                if detailed_result.returncode == 0:
                                    # Extract SSH and HTTP info
                                    pod_output = detailed_result.stdout
                                    
                                    # Build the SSH URL in the correct format (which works more reliably)
                                    # Format: pod-id-hexcode@ssh.runpod.io
                                    # First try to find the hexadecimal code dynamically
                                    hex_suffix = "64411701"  # Default value provided by user
                                    
                                    # Search for specific format in the output
                                    ssh_pattern = re.search(rf'{pod_id}-([a-f0-9]+)@ssh\.runpod\.io', pod_output)
                                    if ssh_pattern:
                                        hex_suffix = ssh_pattern.group(1)
                                        print(f"Discovered hexadecimal suffix: {hex_suffix}")
                                    
                                    pod_ssh_user = f"{pod_id}-{hex_suffix}"
                                    ssh_host = "ssh.runpod.io"
                                    ssh_cmd = f"ssh {pod_ssh_user}@{ssh_host} -i {ssh_key_path}"
                                    print(f"\n🔑 SSH Connection:")
                                    print(f"    {ssh_cmd}")
                                    
                                    # Save these details to .env
                                    save_pod_id_env(pod_id, pod_ssh_user, ssh_host, 22)
                                    
                                    # Look for direct IP:PORT->22 pattern as alternative (less reliable)
                                    ssh_tcp_match = re.search(r'(\d+\.\d+\.\d+\.\d+):(\d+)->22\s+\(pub,tcp\)', pod_output)
                                    if ssh_tcp_match:
                                        host = ssh_tcp_match.group(1)
                                        port = ssh_tcp_match.group(2)
                                        print(f"🔑 Alternative SSH: ssh root@{host} -p {port} -i ~/.ssh/id_ed25519 (less reliable)")
                                    
                                    # Extract HTTP URL for port 3000
                                    http_match = re.search(r'https?://([a-z0-9]+-3000\.proxy\.runpod\.net)', pod_output)
                                    if http_match:
                                        http_url = http_match.group(0)
                                        print(f"🌐 Web Interface: {http_url}")
                                    elif saved_pod_id:
                                        # Construct URL based on pod ID if not found
                                        print(f"🌐 Web Interface: https://{saved_pod_id}-3000.proxy.runpod.net/")
                                
                                if pod_data:
                                    pods.append(pod_data)
                
                if pods:
                    return pods
                
                # Last resort: try running 'runpodctl get pod' directly
                print("Alternative attempt via 'runpodctl get pod'...")
                result = subprocess.run(
                    ["runpodctl", "get", "pod"], 
                    capture_output=True, 
                    text=True
                )
                if result.returncode == 0 and result.stdout.strip():
                    output = result.stdout.strip()
                    lines = output.split('\n')
                    pods = []
                    
                    for i, line in enumerate(lines):
                        if i == 0 or not line.strip():
                            continue
                        
                        parts = line.strip().split()
                        if len(parts) >= 1:
                            pod_id = parts[0]
                            if len(pod_id) >= 12 and not pod_id.startswith('-'):
                                print(f"Pod detected with ID: {pod_id}")
                                status, pod_data = self.get_pod_status_cli(pod_id)
                                if pod_data:
                                    pods.append(pod_data)
                
                return pods
                
            except subprocess.CalledProcessError as e:
                print(f"Error retrieving pods: {e}")
                print(f"Error message: {e.stderr}")
                
                # Alternative method - ask for the current pod directly if we have a saved ID
                saved_pod_id = load_pod_id()
                if saved_pod_id:
                    print(f"Attempt with saved pod: {saved_pod_id}")
                    status, pod_data = self.get_pod_status_cli(saved_pod_id)
                    if pod_data:
                        return [pod_data]
            
            return []
                
        except Exception as e:
            print(f"Unexpected error retrieving pods: {e}")
            traceback.print_exc()
            
            # Last attempt - use the saved ID
            saved_pod_id = load_pod_id()
            if saved_pod_id:
                print(f"Last attempt with saved pod in .env: {saved_pod_id}")
                try:
                    status, pod_data = self.get_pod_status_cli(saved_pod_id)
                    if pod_data:
                        return [pod_data]
                except:
                    pass
            
            return []

    def start_pod(self, pod_id, manual_ssh_port=None, manual_ssh_host=None):
        """Start a pod (CLI version)"""
        # Prefer the CLI version
        return self.start_pod_cli(pod_id, manual_ssh_port=manual_ssh_port, manual_ssh_host=manual_ssh_host)

    def stop_pod(self, pod_id):
        """Stop a pod (CLI version)"""
        # Prefer the CLI version
        return self.stop_pod_cli(pod_id)

    def terminate_pod(self, pod_id):
        """Terminate a pod (CLI version)"""
        # Prefer the CLI version
        return self.terminate_pod_cli(pod_id)

    def list_pods(self):
        """List pods (CLI version)"""
        # Prefer the CLI version
        return self.list_pods_cli()

    def backup_gensyn_data(self, pod_data):
        """Backup critical Gensyn files from a pod"""
        pod_id = pod_data.get("id")
        # Get current pod status
        status, pod_details = self.get_pod_status(pod_id)
        
        # If pod doesn't exist, suggest remediation
        if status == "NOT_FOUND":
            print(f"Error: Pod {pod_id} no longer exists.")
            print("To create a new pod, use: python runpod_manager.py create")
            return False
        
        # Ensure pod is running
        if status != "RUNNING":
            print(f"Pod {pod_id} is not running (status: {status}).")
            print("Cannot backup files from a non-running pod.")
            return False
        
        # Ensure backup directory exists
        if not os.path.exists(GENSYN_BACKUP_DIR):
            print(f"Creating backup directory: {GENSYN_BACKUP_DIR}")
            os.makedirs(GENSYN_BACKUP_DIR, exist_ok=True)
        
        # Get updated SSH information
        print("Attempting to obtain the latest SSH information...")
        ssh_info = self.get_updated_ssh_info(pod_id, max_attempts=10, delay=15)
        if ssh_info:
            # Use the obtained SSH information
            ssh_port = ssh_info.get("ssh_port")
            ssh_host = ssh_info.get("ssh_host")
            ssh_key_path = ssh_info.get("ssh_key_path")
            print(f"✅ SSH information updated: {ssh_host}:{ssh_port}")
        else:
            # If get_updated_ssh_info fails, try with values in pod_data or .env
            print("❌ Unable to obtain updated SSH information.")
            print("Attempting to use available information...")
            
            # Get SSH connection info from pod_data or environment
            ssh_port = pod_data.get("ssh_port")
            ssh_host = pod_data.get("ssh_host")
            
            # If not in pod_data, try environment variables
            if not ssh_port or not ssh_host:
                # Make sure we have the latest environment variables
                dotenv.load_dotenv(override=True)
                ssh_port = os.getenv("SSH_PORT")
                ssh_host = os.getenv("SSH_HOST")
            
            # Ensure we have the necessary SSH information
            if not ssh_port or not ssh_host:
                print("❌ Missing SSH connection information. First run 'python runpod_manager.py connect'")
                return False
            
            # Get SSH key path
            ssh_key_path = get_ssh_key_path()
            
        print(f"Using SSH key: {ssh_key_path}")

        # Files to backup
        backup_files = [
            "/root/rl-swarm/swarm.pem",
            "/root/rl-swarm/modal-login/temp-data/userApiKey.json",
            "/root/rl-swarm/modal-login/temp-data/userData.json"
        ]
        
        # Backup files using direct SCP commands
        success = True
        try:
            for file in backup_files:
                cmd = f"scp -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -P {ssh_port} -i {ssh_key_path} root@{ssh_host}:{file} {GENSYN_BACKUP_DIR}/"
                print(f"Executing backup command: {cmd}")
                subprocess.run(cmd, shell=True, check=True)
                print(f"✅ {file} backed up")
            
            print(f"✅ All Gensyn files have been backed up to {GENSYN_BACKUP_DIR}")
            return True
        except subprocess.CalledProcessError as e:
            success = False
            print(f"❌ Error during backup: {e}")
            print(f"Command failed: {e.cmd}")
            print(f"Return code: {e.returncode}")
        except Exception as e:
            success = False
            print(f"❌ Unexpected error during backup: {e}")
        
        if not success:
            print("⚠️ Backup failed. Please check the error messages above.")
        
        return success

    def clean_pod_info(self):
        """Clean pod information from .env file and pod_info.json"""
        try:
            # 1. Clean .env file
            env_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
            if os.path.exists(env_file):
                # Read current content
                env_data = {}
                with open(env_file, "r") as f:
                    for line in f.readlines():
                        line = line.strip()
                        if line and not line.startswith("#") and "=" in line:
                            key, value = line.split("=", 1)
                            env_data[key.strip()] = value.strip()
                
                # Remove pod information
                keys_to_remove = ["POD_ID", "SSH_USERNAME", "SSH_HOST", "SSH_PORT"]
                for key in keys_to_remove:
                    if key in env_data:
                        del env_data[key]
                
                # Make sure SSH_KEY_PATH is preserved
                if "SSH_KEY_PATH" not in env_data and SSH_KEY_PATH:
                    env_data["SSH_KEY_PATH"] = SSH_KEY_PATH
                
                # Rewrite file
                with open(env_file, "w") as f:
                    for key, value in env_data.items():
                        f.write(f"{key}={value}\n")
                
                print("✅ .env file cleaned of pod information")
            
            # 2. Clean pod_info.json
            pod_info_path = os.path.join(GENSYN_BACKUP_DIR, "pod_info.json")
            if os.path.exists(pod_info_path):
                os.remove(pod_info_path)
                print("✅ pod_info.json file deleted")
            
            print("🔄 Pod information cleaned. You can now create a new pod.")
            return True
        except Exception as e:
            print(f"❌ ERROR cleaning pod information: {e}")
            traceback.print_exc()
            return False

    def restore_gensyn(self, pod_data, skip_username_check=False):
        """Restore Gensyn data to a pod"""
        pod_id = pod_data.get("id")
        # Get current pod status
        status, pod_details = self.get_pod_status(pod_id)
        
        # If pod doesn't exist, clean up and suggest remediation
        if status == "NOT_FOUND":
            print(f"Error: Pod {pod_id} no longer exists.")
            self.clean_pod_info()
            print("Pod information has been removed from the configuration file.")
            print("To continue, please create a new pod with the command 'python runpod_manager.py create'")
            return False
        
        # Ensure pod is running
        if status != "RUNNING":
            print(f"Pod {pod_id} is not running (status: {status}).")
            print("Starting the pod...")
            self.start_pod(pod_id)
            time.sleep(10)  # Give it some time to start
            
            # Verify pod is now running
            status, pod_details = self.get_pod_status(pod_id)
            if status != "RUNNING":
                print(f"Unable to start pod {pod_id}. Current status: {status}")
                return False
            
            print(f"Pod {pod_id} is now running.")
        
        # Get updated SSH information
        print("Attempting to obtain the latest SSH information...")
        ssh_info = self.get_updated_ssh_info(pod_id, max_attempts=10, delay=15)
        if ssh_info:
            # Use the obtained SSH information
            ssh_port = ssh_info.get("ssh_port")
            ssh_host = ssh_info.get("ssh_host")
            ssh_key_path = ssh_info.get("ssh_key_path")
            print(f"✅ SSH information updated: {ssh_host}:{ssh_port}")
        else:
            # If get_updated_ssh_info fails, essayer avec les valeurs dans pod_data ou .env
            print("❌ Unable to obtain updated SSH information.")
            print("Attempting to use available information...")
            
            # Check if pod_data contains SSH connection details
            if isinstance(pod_data, dict) and "ssh_port" in pod_data:
                ssh_port = pod_data.get("ssh_port")
                ssh_host = pod_data.get("ssh_host")
                print(f"Using SSH information from pod_data: {ssh_host}:{ssh_port}")
            
            # If not provided in pod_data, get from environment variables
            if not ssh_port or not ssh_host:
                # Reload environment variables to get the latest values
                dotenv.load_dotenv(override=True)
                ssh_port = os.getenv("SSH_PORT")
                ssh_host = os.getenv("SSH_HOST")
                print(f"Using SSH information from environment: {ssh_host}:{ssh_port}")
            
            # Ensure we have the necessary SSH information
            if not ssh_port or not ssh_host:
                print("❌ Missing SSH information. First run 'python runpod_manager.py connect'")
                return False
            
            # Get SSH key path
            ssh_key_path = get_ssh_key_path()
        
        print(f"Using SSH key: {ssh_key_path}")
        
        # Check if backup files exist
        backup_files = [
            "swarm.pem",
            "userData.json",
            "userApiKey.json"
        ]
        
        # Verify all backup files exist
        missing_files = []
        for filename in backup_files:
            local_path = os.path.join(GENSYN_BACKUP_DIR, filename)
            if not os.path.exists(local_path):
                missing_files.append(filename)
        
        if missing_files:
            print(f"❌ Missing backup files: {', '.join(missing_files)}")
            print(f"Please run a backup on a working pod first.")
            return False
            
        print(f"Restoring Gensyn files to pod {pod_id}...")
        
        # Create remote directories
        try:
            mkdir_cmd = f"ssh -p {ssh_port} -o StrictHostKeyChecking=no -i {ssh_key_path} root@{ssh_host} 'mkdir -p /root/rl-swarm/modal-login/temp-data'"
            print(f"Executing command: {mkdir_cmd}")
            subprocess.run(mkdir_cmd, shell=True, check=True)
            print("✅ Remote directories created")
        except subprocess.CalledProcessError as e:
            print(f"❌ Error creating directories: {e}")
            
            # Try to reconnect using run_pod_connect if command fails
            print("Connection failed. Attempting to get updated SSH information...")
            try:
                ssh_info = self.run_pod_connect(pod_id)
                if ssh_info:
                    ssh_port = ssh_info.get("ssh_port")
                    ssh_host = ssh_info.get("ssh_host")
                    ssh_key_path = ssh_info.get("ssh_key_path")
                    print(f"Updated SSH information: {ssh_host}:{ssh_port}")
                    
                    # Try again with new connection info
                    mkdir_cmd = f"ssh -p {ssh_port} -o StrictHostKeyChecking=no -i {ssh_key_path} root@{ssh_host} 'mkdir -p /root/rl-swarm/modal-login/temp-data'"
                    print(f"Retrying command: {mkdir_cmd}")
                    subprocess.run(mkdir_cmd, shell=True, check=True)
                    print("✅ Remote directories created")
                else:
                    print("❌ Could not get updated SSH information.")
                    return False
            except Exception as e2:
                print(f"❌ Failed to get updated SSH information: {e2}")
                return False
            
        # Restore files using direct SCP commands
        success = True
        try:
            # Restore swarm.pem
            cmd = f"scp -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -P {ssh_port} -i {ssh_key_path} {GENSYN_BACKUP_DIR}/swarm.pem root@{ssh_host}:/root/rl-swarm/"
            print(f"Executing restore command: {cmd}")
            subprocess.run(cmd, shell=True, check=True)
            print("✅ swarm.pem restored")
            
            # Restore userApiKey.json
            cmd = f"scp -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -P {ssh_port} -i {ssh_key_path} {GENSYN_BACKUP_DIR}/userApiKey.json root@{ssh_host}:/root/rl-swarm/modal-login/temp-data/"
            print(f"Executing restore command: {cmd}")
            subprocess.run(cmd, shell=True, check=True)
            print("✅ userApiKey.json restored")
            
            # Restore userData.json
            cmd = f"scp -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -P {ssh_port} -i {ssh_key_path} {GENSYN_BACKUP_DIR}/userData.json root@{ssh_host}:/root/rl-swarm/modal-login/temp-data/"
            print(f"Executing restore command: {cmd}")
            subprocess.run(cmd, shell=True, check=True)
            print("✅ userData.json restored")
            
            # Check that the SSH connection is still active avant de continuer
            print("Checking SSH connection before continuing...")
            try:
                check_cmd = f"ssh -p {ssh_port} -o StrictHostKeyChecking=no -o ConnectTimeout=5 -i {ssh_key_path} root@{ssh_host} 'echo CONNECTION_OK'"
                check_result = subprocess.run(check_cmd, shell=True, capture_output=True, text=True, timeout=10)
                if "CONNECTION_OK" not in check_result.stdout:
                    print("⚠️ SSH connection seems unstable. Attempting to retrieve updated SSH information...")
                    
                    # Essayer de récupérer les dernières informations SSH
                    ssh_info = self.run_pod_connect(pod_id)
                    if ssh_info:
                        ssh_port = ssh_info.get("ssh_port")
                        ssh_host = ssh_info.get("ssh_host")
                        ssh_key_path = ssh_info.get("ssh_key_path")
                        print(f"🔄 SSH information updated: {ssh_host}:{ssh_port}")
                        
                        # Vérifier la nouvelle connexion
                        check_cmd = f"ssh -p {ssh_port} -o StrictHostKeyChecking=no -o ConnectTimeout=5 -i {ssh_key_path} root@{ssh_host} 'echo CONNECTION_OK'"
                        check_result = subprocess.run(check_cmd, shell=True, capture_output=True, text=True, timeout=10)
                        if "CONNECTION_OK" not in check_result.stdout:
                            print("⚠️ Unable to establish a stable SSH connection. Service restart is not possible.")
                            print("✅ Files have been successfully restored, but services will need to be restarted manually.")
                            return True
                    else:
                        print("⚠️ Unable to retrieve new SSH information. Service restart is not possible.")
                        print("✅ Files have been successfully restored, but services will need to be restarted manually.")
                        return True
            except Exception as e:
                print(f"⚠️ Exception when checking SSH connection: {e}")
                print("✅ Files have been successfully restored, but services will need to be restarted manually.")
                return True
            
            print("✅ Stable SSH connection established, continuing with service restart...")
            
            # Use the restart script instead of executing commands directly
            try:
                # Copy the restart script to the remote server
                script_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "restart_gensyn.sh")
                if not os.path.exists(script_path):
                    print("❌ Restart script not found. Creating script...")
                    with open(script_path, "w") as f:
                        f.write("""#!/bin/bash

# Script to restart Gensyn services
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

echo "==== Gensyn Services Restart Complete ====" """)
                    # Make the script executable
                    os.chmod(script_path, 0o755)
                    print("✅ Restart script created successfully")
                
                # Copy the script to the remote server
                print("Copying restart script to remote server...")
                copy_cmd = f"scp -P {ssh_port} -i {ssh_key_path} {script_path} root@{ssh_host}:/tmp/restart_gensyn.sh"
                print(f"Executing command: {copy_cmd}")
                subprocess.run(copy_cmd, shell=True, check=True)
                print("✅ Restart script copied successfully")
                
                # Make the script executable on the remote server
                chmod_cmd = f"ssh -p {ssh_port} -i {ssh_key_path} root@{ssh_host} 'chmod +x /tmp/restart_gensyn.sh'"
                print(f"Making script executable: {chmod_cmd}")
                subprocess.run(chmod_cmd, shell=True, check=True)
                
                # Execute the script
                run_cmd = f"ssh -p {ssh_port} -i {ssh_key_path} root@{ssh_host} '/tmp/restart_gensyn.sh'"
                print(f"Executing restart script: {run_cmd}")
                subprocess.run(run_cmd, shell=True, check=True)
                print("✅ Restart script executed successfully")
                
            except subprocess.CalledProcessError as e:
                print(f"⚠️ Warning: Error during restart procedures: {e}")
                print("⚠️ Some restart commands may have failed. You might need to restart services manually by connecting to the pod.")
                
            print("✅ Service restart procedures completed")
                
        except subprocess.CalledProcessError as e:
            success = False
            print(f"❌ Error during restoration: {e}")
        
        if success:
            print(f"✅ All Gensyn files have been restored to pod {pod_id}")
            return True
        else:
            print("❌ Errors occurred during the restoration of Gensyn files.")
            return False

    def create_pod_cli(self, config=None, secure_cloud=False):
        """Create a pod using the RunPod CLI command"""
        if config is None:
            config = DEFAULT_POD_CONFIG
            
        # Get the SSH public key to pass to the pod
        ssh_key_path = get_ssh_key_path()
        public_key_path = f"{ssh_key_path}.pub"
        public_key = ""
        
        if os.path.exists(public_key_path):
            try:
                with open(public_key_path, "r") as f:
                    public_key = f.read().strip()
                print(f"Using SSH public key: {public_key[:40]}...")
            except Exception as e:
                print(f"Error reading SSH public key: {e}")
        else:
            print(f"⚠️ SSH public key file not found at {public_key_path}")
            print("Creating SSH key pair...")
            ensure_ssh_key_exists()
            # Try reading again after creation
            if os.path.exists(public_key_path):
                with open(public_key_path, "r") as f:
                    public_key = f.read().strip()
        
        # Build the CLI command with correct syntax
        cmd = ["runpodctl", "create", "pod", 
               "--name", config['name'],
               "--templateId", config['templateId'],
               "--gpuType", config['gpu'],
               "--imageName", config['image']]
        
        # Add cloud type flag
        if secure_cloud:
            cmd.append("--secureCloud")
        else:
            cmd.append("--communityCloud")
            
        # Add volume size if specified
        if config.get('diskInGb'):
            cmd.extend(["--volumeSize", str(config['diskInGb'])])
            
        # Add container disk size if specified
        if config.get('containerDiskInGb'):
            cmd.extend(["--containerDiskSize", str(config['containerDiskInGb'])])
            
        # Add environment variables
        if public_key:
            cmd.extend(["--env", f"PUBLIC_KEY={public_key}"])
        
        print(f"Executing command: {' '.join(cmd)}")
        
        try:
            # Execute the command
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            print(result.stdout)
            
            # Extract pod ID from output (format: "pod "ID" created for $X.XX / hr")
            match = re.search(r'pod "([^"]+)" created', result.stdout)
            if match:
                pod_id = match.group(1)
                print(f"Pod successfully created! ID: {pod_id}")
                
                # Save pod ID to environment file
                save_pod_id_env(pod_id)
                
                # Wait a moment for the pod to initialize
                print(f"Waiting 10 seconds for pod {pod_id} to initialize...")
                time.sleep(10)
                
                # Save pod info for future reference
                status, pod_data = self.get_pod_status(pod_id)
                if status and pod_data:
                    print(f"Initial pod status: {status}")
                    
                    # Get SSH info and save to .env - but don't attempt connection
                    # Wait an additional 30 seconds for SSH information to be available
                    print("Waiting 30 seconds for SSH information to be available...")
                    time.sleep(30)
                    
                    result = subprocess.run(
                        ["runpodctl", "get", "pod", pod_id, "-o", "wide"], 
                        capture_output=True, 
                        text=True
                    )
                    
                    if result.returncode == 0 and "SSH" in result.stdout:
                        # Extract SSH URL from format: ssh username@host
                        ssh_match = re.search(r'ssh\s+([a-z0-9]+(?:-[a-f0-9]+)?)@([a-z0-9\.]+)', result.stdout)
                        if ssh_match:
                            username = ssh_match.group(1)
                            host = ssh_match.group(2)
                            save_pod_id_env(pod_id, username, host, 22)
                            print(f"SSH information retrieved and saved: {username}@{host}")
                        else:
                            save_pod_id_env(pod_id)
                            print("SSH format not detected, saving only pod ID.")
                    else:
                        save_pod_id_env(pod_id)
                        print("SSH information not available at the moment, saving only pod ID.")
                
                return pod_id
            else:
                print("Pod created but couldn't extract ID from output")
                return None
                
        except subprocess.CalledProcessError as e:
            print(f"CLI command failed: {e}")
            print(f"Error output: {e.stderr}")
            return None

    def install_runpod_cli(self):
        """Install and configure the RunPod CLI"""
        print("Installing RunPod CLI...")
        try:
            # Check if runpodctl is already installed
            result = subprocess.run(["which", "runpodctl"], capture_output=True, text=True)
            if result.returncode == 0:
                print("RunPod CLI is already installed.")
            else:
                # Install the CLI
                print("Installing RunPod CLI...")
                subprocess.run("wget -qO- cli.runpod.net | bash", shell=True, check=True)
                print("RunPod CLI installed successfully.")
            
            # Configure the API key
            print("Configuring RunPod API key...")
            subprocess.run(["runpodctl", "config", "--apiKey", self.api_key], check=True)
            print("API key configured successfully.")
            
            # Ensure the SSH key exists
            ensure_ssh_key_exists()
            
            return True
        except subprocess.CalledProcessError as e:
            print(f"Error installing/configuring RunPod CLI: {e}")
            return False
            
    def create_pod_api(self, config=None, retry_attempts=3, retry_delay=60, accept_alternate_gpus=False, secure_cloud=False, 
                 datacenter=None, interruptible=False, min_vcpu=4, min_ram=32):
        """Create a new pod with the specified configuration using API (not recommended)"""
        if config is None:
            config = DEFAULT_POD_CONFIG
            
        print(f"Creating pod: {config['name']} using template {config['templateId']}...")
        
        # Try with retry logic
        for attempt in range(retry_attempts):
            # Build payload matching exactly what worked in CLI
            payload = {
                "name": config['name'],
                "templateId": config['templateId'],
                "gpuTypeId": config['gpu'],  # Corresponds to --gpuType in CLI
                "imageName": config['image'],  # Corresponds to --imageName in CLI
                "cloudType": "SECURE" if secure_cloud else "COMMUNITY"  # --communityCloud in CLI
            }
            
            # Only add these if not using template or if they need to be overridden
            if config.get('containerDiskInGb'):
                payload["containerDiskInGb"] = config['containerDiskInGb']
            
            if config.get('diskInGb'):
                payload["diskInGb"] = config['diskInGb']
                
            print(f"Attempt {attempt+1}/{retry_attempts}")
            print(f"Using payload: {json.dumps(payload, indent=2)}")
            
            try:
                # Use the correct API endpoint
                response = requests.post(
                    f"{RUNPOD_API_URL}/pods",
                    headers=self.headers,
                    json=payload
                )
                
                print(f"API Response: {response.status_code}")
                print(f"Response Content: {response.text}")
                
                # If successful (201 Created)
                if response.status_code == 201:
                    pod_data = response.json()
                    pod_id = pod_data.get("id")
                    print(f"Pod created successfully! ID: {pod_id}")
                    print(f"Cost per hour: ${pod_data.get('costPerHr', 'N/A')}")
                    
                    # Save pod info to a file for future reference
                    with open(f"{GENSYN_BACKUP_DIR}/pod_info.json", "w") as f:
                        json.dump(pod_data, f, indent=2)
                    
                    return pod_id
                
                # If there are no instances available, retry
                if response.status_code == 500 and "no instances currently available" in response.text:
                    print(f"No instances available. Continuing...")
                    continue
                    
                # For other errors, raise the exception to trigger retry logic
                response.raise_for_status()
                
            except requests.RequestException as e:
                print(f"API Request error: {e}")
                
            # Wait before the next retry if not the last attempt
            if attempt < retry_attempts - 1:
                wait_time = retry_delay * (attempt + 1)  # Progressive delay
                print(f"Waiting {wait_time} seconds before retry...")
                time.sleep(wait_time)
        else:
                print("Maximum retry attempts reached. No instances available.")
                
        # If we get here, all retries failed
        print("Failed to create pod after multiple attempts. Consider trying later.")
        return None

    def get_pod_ssh_info_cli(self, pod_id):
        """Retrieve SSH information for a pod using runpodctl"""
        print(f"Retrieving SSH information for pod {pod_id}...")
        
        # Use the SSH key specified in .env
        ssh_key_path = os.path.expanduser(SSH_KEY_PATH)
        
        try:
            # Try to get information via runpodctl
            result = subprocess.run(
                ["runpodctl", "get", "pod", pod_id, "-a"], 
                capture_output=True, 
                text=True, 
                check=True
            )
            output = result.stdout
            print(f"runpodctl output for pod {pod_id}:\n{output}")
            
            # 1. Check for direct TCP port (format IP:PORT->22)
            ssh_tcp_match = re.search(r'(\d+\.\d+\.\d+\.\d+):(\d+)->22\s*(?:\(pub,\s*tcp\)|.*tcp)', output)
            if ssh_tcp_match:
                host = ssh_tcp_match.group(1)
                port = ssh_tcp_match.group(2)
                username = "root"  # Pour les connexions directes, c'est toujours root
                print(f"Direct IP found: {username}@{host}:{port}")
                
                # Save these details to .env
                save_pod_id_env(pod_id, username, host, port)
                
                return {
                    "username": username,
                    "host": host,
                    "port": port,
                    "key_path": ssh_key_path
                }
            
            # 2. If information is already saved in .env and corresponds to this pod
            if SSH_USERNAME and SSH_HOST and pod_id == os.getenv("POD_ID"):
                print(f"Using SSH information from .env: {SSH_USERNAME}@{SSH_HOST}:{SSH_PORT}")
                return {
                    "username": SSH_USERNAME,
                    "host": SSH_HOST,
                    "port": int(SSH_PORT) if SSH_PORT else 22,
                    "key_path": ssh_key_path
                }
                
            # 3. Check for RunPod tunnel format
            ssh_pattern = re.search(r'ssh\s+([a-z0-9]+-[a-f0-9]+)@([a-z0-9\.]+)', output)
            if ssh_pattern:
                username = ssh_pattern.group(1)
                host = ssh_pattern.group(2)
                port = 22
                print(f"SSH tunnel found via regex: {username}@{host}:{port}")
                
                # Save these details to .env
                save_pod_id_env(pod_id, username, host, port)
                
                return {
                    "username": username,
                    "host": host,
                    "port": port,
                    "key_path": ssh_key_path
                }
            
            # 4. Legacy RunPod format with ID-suffix
            ssh_legacy_match = re.search(rf'{pod_id}-([a-f0-9]+)@ssh\.runpod\.io', output)
            if ssh_legacy_match:
                hex_suffix = ssh_legacy_match.group(1)
                username = f"{pod_id}-{hex_suffix}"
                host = "ssh.runpod.io"
                port = 22
                print(f"Legacy SSH format found: {username}@{host}")
                
                # Save these details to .env
                save_pod_id_env(pod_id, username, host, port)
                
                return {
                    "username": username,
                    "host": host,
                    "port": port,
                    "key_path": ssh_key_path
                }
            
            # 5. Return information from .env with warning
            if SSH_USERNAME and SSH_HOST:
                print(f"⚠️ SSH format not detected in CLI output. Using values from .env.")
                return {
                    "username": SSH_USERNAME,
                    "host": SSH_HOST,
                    "port": int(SSH_PORT) if SSH_PORT else 22,
                    "key_path": ssh_key_path
                }
            
            # If we got here, we couldn't find SSH info in standard formats
            print("⚠️ No SSH information found in standard formats. Please check RunPod console.")
            return None
            
        except Exception as e:
            print(f"Error retrieving SSH information: {e}")
            traceback.print_exc()
            
            # In case of an error, try to get the info from environment variables
            if SSH_USERNAME and SSH_HOST and SSH_PORT:
                print(f"Using SSH information from .env as fallback: {SSH_USERNAME}@{SSH_HOST}:{SSH_PORT}")
                return {
                    "username": SSH_USERNAME,
                    "host": SSH_HOST,
                    "port": int(SSH_PORT) if SSH_PORT else 22,
                    "key_path": ssh_key_path
                }
            
            return None

    def get_pod_ssh_username(self, pod_id):
        """Retrieve exact SSH format for this pod"""
        # First, check if we've already saved the username
        saved_username = get_saved_ssh_username(pod_id)
        if saved_username:
            return saved_username
            
        try:
            # Try to get information via API
            response = requests.get(
                f"{RUNPOD_API_URL}/pods/{pod_id}",
                headers=self.headers
            )
            if response.status_code == 200:
                pod_data = response.json()
                if "sshUrl" in pod_data:
                    ssh_url = pod_data["sshUrl"]
                    # Typical format: ssh://pod_id-hexdigits@ssh.runpod.io
                    if "@" in ssh_url:
                        username = ssh_url.split("@")[0]
                        if username.startswith("ssh://"):
                            username = username[6:]  # Remove the ssh:// prefix
                        print(f"SSH username extracted from API: {username}")
                        # Save for next time
                        save_pod_id_env(pod_id, username)
                        return username
            
            # If the API doesn't provide the information, try via CLI
            result = subprocess.run(
                ["runpodctl", "get", "pod", pod_id, "-a"], 
                capture_output=True, 
                text=True, 
                check=True
            )
            output = result.stdout
            
            # Search for lines containing ssh://, full SSH URL
            for line in output.split("\n"):
                if "ssh://" in line:
                    # Try to extract the full SSH URL
                    ssh_parts = re.findall(r'ssh://([^@]+)@', line)
                    if ssh_parts:
                        username = ssh_parts[0]
                        print(f"SSH username extracted from CLI: {username}")
                        # Save for next time
                        save_pod_id_env(pod_id, username)
                        return username
            
            # Try extracting SSH information from webpage if available
            web_ssh_info = self.extract_ssh_from_webpage(pod_id)
            if web_ssh_info and "username" in web_ssh_info:
                username = web_ssh_info["username"]
                print(f"SSH username extracted from web: {username}")
                # Save for next time
                save_pod_id_env(pod_id, username)
                return username
            
            # If pod ID is te4rokqbt4wkc7, use known suffix
            if pod_id == "te4rokqbt4wkc7":
                username = f"{pod_id}-644119a3"
                print(f"Using known suffix for {pod_id}: {username}")
                return username
            
            # If we didn't find specific information, use generic format
            print(f"Impossible to determine exact username, using generic format")
            
            # Generic pod_id-user, might not work
            return f"{pod_id}-user"
            
        except Exception as e:
            print(f"Error retrieving SSH username: {e}")
            
            # If pod ID is te4rokqbt4wkc7, use known suffix
            if pod_id == "te4rokqbt4wkc7":
                username = f"{pod_id}-644119a3"
                print(f"Using known suffix for {pod_id}: {username}")
                return username
                
            # Fallback to generic format
            return f"{pod_id}-user"

    def extract_ssh_from_webpage(self, pod_id):
        """Advanced method: extract SSH information from RunPod webpage (requires selenium)"""
        try:
            # Check if selenium is installed
            import importlib.util
            selenium_spec = importlib.util.find_spec("selenium")
            if selenium_spec is None:
                print("To use web extraction, install selenium: pip install selenium")
                return None
                
            # Import necessary modules if selenium is available
            from selenium import webdriver
            from selenium.webdriver.chrome.options import Options
            from selenium.webdriver.common.by import By
            from selenium.webdriver.support.ui import WebDriverWait
            from selenium.webdriver.support import expected_conditions as EC
            import time
            
            print("Attempting to extract SSH information from RunPod webpage...")
            
            # Configure headless browser
            chrome_options = Options()
            chrome_options.add_argument("--headless")
            chrome_options.add_argument("--no-sandbox")
            chrome_options.add_argument("--disable-dev-shm-usage")
            
            # Initialize driver
            driver = webdriver.Chrome(options=chrome_options)
            
            try:
                # Connect to RunPod
                driver.get("https://runpod.io/console/login")
                
                # Wait for the page to load
                WebDriverWait(driver, 10).until(
                    EC.presence_of_element_located((By.ID, "email"))
                )
                
                # Enter credentials (replace with your own or read from .env)
                email_input = driver.find_element(By.ID, "email")
                email_input.send_keys(os.getenv("RUNPOD_EMAIL", ""))
                
                password_input = driver.find_element(By.ID, "password")
                password_input.send_keys(os.getenv("RUNPOD_PASSWORD", ""))
                
                # Click the login button
                login_button = driver.find_element(By.XPATH, "//button[contains(text(), 'Sign In')]")
                login_button.click()
                
                # Wait for connection to be established
                time.sleep(5)
                
                # Navigate to the pod page
                driver.get(f"https://runpod.io/console/pods/{pod_id}")
                
                # Wait for the page to load
                time.sleep(5)
                
                # Find the SSH connection button
                ssh_button = driver.find_element(By.XPATH, "//button[contains(text(), 'Connect')]")
                ssh_button.click()
                
                # Wait for the modal to appear
                WebDriverWait(driver, 10).until(
                    EC.presence_of_element_located((By.XPATH, "//div[contains(text(), 'SSH')]"))
                )
                
                # Extract SSH URL
                ssh_text = driver.find_element(By.XPATH, "//div[contains(text(), 'ssh ')]").text
                
                # Expected format: ssh pod_id-hexdigits@ssh.runpod.io -i ~/.ssh/id_ed25519
                if "ssh " in ssh_text and "@" in ssh_text:
                    ssh_parts = ssh_text.split(" ")
                    ssh_url = ssh_parts[1]
                    
                    # Extract username and host
                    username, host = ssh_url.split("@")
                    
                    print(f"SSH information extracted from webpage: {username}@{host}")
                    return {
                        "username": username,
                        "host": host,
                        "port": 22,
                        "key_path": SSH_KEY_PATH
                    }
                
            finally:
                # Close the browser
                driver.quit()
                
        except Exception as e:
            print(f"Error extracting SSH information from webpage: {e}")
            
        return None

    def check_ssh_port_open(self, host, port, timeout=5):
        """Check if SSH port is actually open and accessible"""
        try:
            print(f"Checking if SSH port {port} is open on {host}...")
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(timeout)
            s.connect((host, port))
            s.close()
            print(f"Port {port} is open on {host}")
            return True
        except socket.error as e:
            print(f"Port {port} not accessible on {host}: {e}")
            return False
            
    def query_runpod_ssh_port(self, pod_id):
        """Directly queries the RunPod API to get the SSH port after a restart"""
        print(f"Searching for new SSH port for pod {pod_id}...")
        try:
            # Try to get information via RunPod API
            cmd = ["runpodctl", "get", "pod", pod_id, "-a"]
            print(f"Executing command: {' '.join(cmd)}")
            
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode == 0:
                output = result.stdout
                
                # Look for lines containing TCP connections
                tcp_pattern = re.search(r'(\d+\.\d+\.\d+\.\d+):(\d+)->22\s*(?:\(pub,\s*tcp\)|.*tcp)', output)
                if tcp_pattern:
                    host = tcp_pattern.group(1)
                    port = tcp_pattern.group(2)
                    print(f"SSH port detected via TCP: {host}:{port}")
                    return {
                        "host": host,
                        "port": port,
                        "username": "root"
                    }
                    
                # Look for other SSH connection formats
                if "SSH" in output:
                    ssh_pattern = re.search(r'ssh\s+([^@]+)@([^\s]+)', output)
                    if ssh_pattern:
                        username = ssh_pattern.group(1)
                        host = ssh_pattern.group(2)
                        print(f"SSH information detected: {username}@{host}")
                        return {
                            "username": username, 
                            "host": host, 
                            "port": "22"
                        }
            
            print("No SSH information found in runpodctl output")
            return None
            
        except Exception as e:
            print(f"Error while searching for SSH port: {e}")
            return None

    def get_updated_ssh_info(self, pod_id, max_attempts=20, delay=30):
        """Tries multiple methods to obtain updated SSH information"""
        print(f"Looking for updated SSH information for pod {pod_id}...")
        
        for attempt in range(1, max_attempts+1):
            print(f"Attempt {attempt}/{max_attempts} to obtain SSH information...")
            
            # Method 1: Use query_runpod_ssh_port
            ssh_info = self.query_runpod_ssh_port(pod_id)
            if ssh_info and ssh_info.get("host") and ssh_info.get("port"):
                print(f"✅ SSH information obtained: {ssh_info.get('username', 'root')}@{ssh_info['host']}:{ssh_info['port']}")
                
                # Check if the connection works
                try:
                    ssh_key_path = get_ssh_key_path()
                    check_cmd = f"ssh -o StrictHostKeyChecking=no -o ConnectTimeout=5 -i {ssh_key_path} {ssh_info.get('username', 'root')}@{ssh_info['host']} -p {ssh_info['port']} 'echo SSH_OK'"
                    print(f"Testing connection: {check_cmd}")
                    
                    check_result = subprocess.run(check_cmd, shell=True, capture_output=True, text=True, timeout=10)
                    if "SSH_OK" in check_result.stdout:
                        print("✅ SSH connection successfully established!")
                        
                        # Update information in .env
                        save_pod_id_env(
                            pod_id, 
                            username=ssh_info.get('username', 'root'),
                            host=ssh_info['host'], 
                            port=ssh_info['port']
                        )
                        
                        return {
                            "ssh_user": ssh_info.get('username', 'root'),
                            "ssh_host": ssh_info['host'],
                            "ssh_port": ssh_info['port'],
                            "ssh_key_path": ssh_key_path
                        }
                except Exception as e:
                    print(f"Error during connection test: {e}")
            
            # If we got here, no method worked
            print(f"Waiting {delay} seconds before next attempt...")
            time.sleep(delay)
            
        print("❌ Unable to obtain updated SSH information after multiple attempts.")
        return None

    def get_pod_ssh_info_from_example(self, pod_id, example=None):
        """Extract SSH information directly from provided example"""
        
        # If an example is provided, analyze it
        if example:
            print(f"Analyzing SSH example: {example}")
            # Expected format: ssh root@194.26.196.173 -p 31432 -i ~/.ssh/id_ed25519
            # or ssh wj9x7lvqqhh4cg-64410eea@ssh.runpod.io -i ~/.ssh/id_ed25519
            
            ssh_parts = example.split()
            username_host = None
            port = 22  # Default port
            key_path = SSH_KEY_PATH  # Default path for key
            
            for i, part in enumerate(ssh_parts):
                # Ignore the ssh command itself
                if i == 0 and part == "ssh":
                    continue
                    
                # Look for user@host format
                if "@" in part and not part.startswith("-"):
                    username_host = part
                    
                # Look for port option
                if part == "-p" and i+1 < len(ssh_parts):
                    try:
                        port = int(ssh_parts[i+1])
                    except ValueError:
                        pass
                        
                # Look for key option
                if part == "-i" and i+1 < len(ssh_parts):
                    key_path = os.path.expanduser(ssh_parts[i+1])
            
            # If we found username@host
            if username_host and "@" in username_host:
                username, host = username_host.split("@", 1)
                
                print(f"SSH information extracted: username={username}, host={host}, port={port}, key={key_path}")
                return {
                    "username": username,
                    "host": host,
                    "port": port,
                    "key_path": key_path
                }
        
        # If no example is provided or the example couldn't be analyzed
        # Try to build SSH information for this specific pod
        if pod_id == "wj9x7lvqqhh4cg":
            # Specific example provided for this pod
            return {
                "username": "root",
                "host": "194.26.196.173",
                "port": 31432,
                "key_path": SSH_KEY_PATH
            }
        
        # Otherwise use CLI to retrieve information
        return self.get_pod_ssh_info_cli(pod_id)

    def connect(self, pod_data=None, force=False):
        """Connect to a pod via SSH"""
        # If pod_data is not provided, try to load from saved info
        if not pod_data:
            pod_id = load_pod_id()
            
            if not pod_id:
                print("❌ No pod ID found. Please create a pod first.")
                return None
                
            # Get pod info from RunPod
            status, pod_data = self.get_pod_status(pod_id)
            
            if not pod_data:
                print(f"❌ Unable to retrieve info for pod {pod_id}")
                return None
                
            if status != "RUNNING":
                print(f"⚠️ Pod {pod_id} is not running (status: {status}).")
                print("⚠️ SSH connection may not be available.")
                if not force:
                    print("Use --force to attempt connection anyway.")
                    return None
                    
            pod_data["id"] = pod_id
                    
        # Get SSH info using runpodctl
        ssh_info = self.get_pod_ssh_info_cli(pod_data["id"])
        
        if ssh_info:
            # Extract SSH info from result
            ssh_user = ssh_info.get("username")
            ssh_host = ssh_info.get("host")
            ssh_port = ssh_info.get("port")
            ssh_key_path = ssh_info.get("key_path")
            
            # Save SSH info to .env for future use
            save_pod_id_env(pod_data["id"], ssh_user, ssh_host, ssh_port)
            
            # Display SSH connection command
            if all([ssh_user, ssh_host, ssh_port, ssh_key_path]):
                print(f"\n✅ SSH connection info updated. Use the following command to connect:\n")
                ssh_cmd = f"ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null {ssh_user}@{ssh_host} -p {ssh_port} -i {ssh_key_path}"
                print(f"    {ssh_cmd}")
                
                # If this is a direct connection, try to get HTTP info as well (for web interfaces)
                if ssh_host != "ssh.runpod.io":
                    print("\n✅ You can also use SCP for file transfers:\n")
                    scp_cmd = f"scp -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -P {ssh_port} -i {ssh_key_path} [local_file] {ssh_user}@{ssh_host}:[remote_path]"
                    print(f"    {scp_cmd}")
                    
                    # Try to get HTTP URL for port 3000 (common for web interfaces)
                    try:
                        # Run command to get detailed pod info
                        result = subprocess.run(
                            ["runpodctl", "get", "pod", pod_data["id"], "-a"], 
                            capture_output=True, 
                            text=True, 
                            check=True
                        )
                        output = result.stdout
                        
                        # Look for HTTP port mappings (usually 3000 for web interfaces)
                        http_match = re.search(r'(\d+\.\d+\.\d+\.\d+):(\d+)->3000\s*(?:\(prv,\s*http\)|.*http)', output)
                        if http_match:
                            http_host = http_match.group(1)
                            http_port = http_match.group(2)
                            http_url = f"http://{http_host}:{http_port}"
                            print(f"\n🌐 Web interface available at:\n")
                            print(f"    {http_url}")
                        
                        # Also check for public URLs in the tunnel format
                        pod_id = pod_data["id"]
                        public_url = f"https://{pod_id}-3000.proxy.runpod.io"
                        print(f"\n🌐 Alternative web access may be available at:\n")
                        print(f"    {public_url}")
                        
                    except Exception as e:
                        # Non-critical error, just log it
                        print(f"\n⚠️ Could not retrieve web interface URL: {e}")
                
                # Return the SSH info for later use
                return ssh_info
            else:
                print("❌ Incomplete SSH information received.")
                return None
        else:
            print("❌ Failed to retrieve SSH information.")
            return None

def load_pod_id():
    """Load pod ID from .env file or try to find it from running pods if not found in .env"""
    # First, try to load pod ID from .env file
    env_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(env_file):
        with open(env_file, "r") as f:
            for line in f:
                line = line.strip()
                if line and line.startswith("POD_ID="):
                    pod_id = line.split("=", 1)[1].strip()
                    if pod_id:  # If the ID is not empty
                        return pod_id
    
    # If no ID is found, try to read from pod_info.json
    pod_info_path = os.path.join(GENSYN_BACKUP_DIR, "pod_info.json")
    if os.path.exists(pod_info_path):
        try:
            with open(pod_info_path, "r") as f:
                pod_info = json.load(f)
                if "id" in pod_info:
                    return pod_info["id"]
        except:
            pass
    
    # If still no ID, try to retrieve it from running pods
    try:
        # Execute runpodctl get pod
        result = subprocess.run(
            ["runpodctl", "get", "pod"], 
            capture_output=True, 
            text=True
        )
        
        if result.returncode == 0 and result.stdout.strip():
            # Analyze output to extract the ID of the first pod
            lines = result.stdout.strip().split('\n')
            if len(lines) >= 2:  # At least one header line + one pod line
                pod_line = lines[1].strip()
                parts = pod_line.split()
                if len(parts) >= 1:
                    pod_id = parts[0]
                    # Check if it's a valid ID (at least 12 characters)
                    if len(pod_id) >= 12:
                        print(f"Pod found with ID: {pod_id}")
                        # Save the ID in .env for future use
                        save_pod_id_env(pod_id)
                        return pod_id
    except Exception as e:
        print(f"Error searching for active pod: {e}")
    
    return None

def save_pod_id_env(pod_id, username=None, host=None, port=None):
    """Save the pod ID and SSH connection details to the .env file"""
    # Save the pod ID to .env for future use
    dotenv.set_key(".env", "POD_ID", pod_id)
    
    env_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    env_vars = {}
    
    # Read existing .env file if it exists
    if os.path.exists(env_file):
        with open(env_file, "r") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    env_vars[key.strip()] = value.strip()
    
    # Update or add POD_ID
    if pod_id:
        env_vars["POD_ID"] = pod_id
    
    # Update SSH information if present
    if username:
        env_vars["SSH_USERNAME"] = username
    if host:
        env_vars["SSH_HOST"] = host
    if port:
        env_vars["SSH_PORT"] = str(port)
    
    # Ensure SSH_KEY_PATH is defined and preserved
    if "SSH_KEY_PATH" not in env_vars or not env_vars["SSH_KEY_PATH"]:
        env_vars["SSH_KEY_PATH"] = SSH_KEY_PATH
    
    # Write updated .env file
    with open(env_file, "w") as f:
        for key, value in env_vars.items():
            f.write(f"{key}={value}\n")
    
    print(f"Pod ID {pod_id} and SSH information saved in {env_file}")

def get_saved_ssh_username(pod_id):
    """Retrieve SSH username from environment variables"""
    # If we have an SSH username and it belongs to the current pod
    if SSH_USERNAME and pod_id == os.getenv("POD_ID"):
        print(f"SSH username retrieved from .env: {SSH_USERNAME}")
        return SSH_USERNAME
    
    # Otherwise, try to build the username from standard format
    return f"{pod_id}-user"

def clean_pod_info():
    """Clean pod information from .env file and pod_info.json"""
    try:
        # 1. Clean .env file
        env_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
        if os.path.exists(env_file):
            # Read current content
            env_data = {}
            with open(env_file, "r") as f:
                for line in f.readlines():
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        key, value = line.split("=", 1)
                        env_data[key.strip()] = value.strip()
            
            # Remove pod information
            keys_to_remove = ["POD_ID", "SSH_USERNAME", "SSH_HOST", "SSH_PORT"]
            for key in keys_to_remove:
                if key in env_data:
                    del env_data[key]
            
            # Make sure SSH_KEY_PATH is preserved
            if "SSH_KEY_PATH" not in env_data and SSH_KEY_PATH:
                env_data["SSH_KEY_PATH"] = SSH_KEY_PATH
            
            # Rewrite file
            with open(env_file, "w") as f:
                for key, value in env_data.items():
                    f.write(f"{key}={value}\n")
            
            print("✅ .env file cleaned of pod information")
        
        # 2. Clean pod_info.json
        pod_info_path = os.path.join(GENSYN_BACKUP_DIR, "pod_info.json")
        if os.path.exists(pod_info_path):
            os.remove(pod_info_path)
            print("✅ pod_info.json file deleted")
        
        print("🔄 Pod information cleaned. You can now create a new pod.")
        return True
    except Exception as e:
        print(f"❌ ERROR cleaning pod information: {e}")
        traceback.print_exc()
        return False

def get_ssh_key_path():
    """Get the SSH key path from environment or use default"""
    # Load environment variables from .env file
    env_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(env_file):
        with open(env_file, "r") as f:
            for line in f:
                line = line.strip()
                if line.startswith("SSH_KEY_PATH="):
                    env_key_path = line.split("=", 1)[1]
                    if env_key_path:
                        return os.path.expanduser(env_key_path)
    
    # Check environment variable
    env_key_path = os.getenv("SSH_KEY_PATH")
    if env_key_path:
        return os.path.expanduser(env_key_path)
    
    # Default path
    return os.path.expanduser("~/.ssh/id_rsa")

def main():
    """Main function to handle CLI arguments"""
    parser = argparse.ArgumentParser(description='RunPod Manager - Automated GPU instance management')
    
    # Subparsers for different commands
    subparsers = parser.add_subparsers(dest='command', help='Command to execute')
    
    # Create pod command
    create_parser = subparsers.add_parser('create', help='Create a new pod')
    create_parser.add_argument('--name', type=str, help='Name for the pod')
    create_parser.add_argument('--gpu', type=str, help='GPU type to use')
    create_parser.add_argument('--disk', type=int, help='Disk size in GB')
    create_parser.add_argument('--secure', action='store_true', help='Use secure cloud')
    
    # Start pod command
    start_parser = subparsers.add_parser('start', help='Start an existing pod')
    
    # Stop pod command
    stop_parser = subparsers.add_parser('stop', help='Stop a running pod')
    
    # Terminate pod command
    terminate_parser = subparsers.add_parser('terminate', help='Terminate (delete) a pod')
    
    # List pods command
    list_parser = subparsers.add_parser('list', help='List pods')
    
    # Add backup command (deprecated but kept for compatibility)
    backup_parser = subparsers.add_parser('backup', help='Backup critical Gensyn files')
    backup_parser.add_argument('--force', action='store_true', help='Force backup even without direct TCP connection')
    
    # Rename deploy to restore (deprecated but kept for compatibility)
    restore_parser = subparsers.add_parser('restore', help='Restore critical Gensyn files')
    restore_parser.add_argument('--force', action='store_true', help='Force restore even without direct TCP connection')
    
    # Add ssh command
    ssh_parser = subparsers.add_parser('connect', help='Get SSH connection information for a pod')
    
    # Keep 'ssh' command as an alias for backward compatibility
    ssh_alias_parser = subparsers.add_parser('ssh', help='Alias for connect command')
    
    # Add clean command
    subparsers.add_parser('clean', help='Clean pod information from configuration files')
    
    # IMPORTANT: For backward compatibility, keep 'deploy' for now
    deploy_parser = subparsers.add_parser('deploy', help='[DEPRECATED] See README for manual restoration')
    
    # Parse arguments
    args = parser.parse_args()
    
    # Ensure the API key is available
    if not API_KEY:
        print("ERROR: RUNPOD_API_KEY environment variable not set. Please set it before running this script.")
        sys.exit(1)
    
    # Create RunPodManager instance
    manager = RunPodManager(API_KEY)
    
    # Process the command
    if args.command == 'create':
        # Prepare configuration based on defaults and arguments
        config = DEFAULT_POD_CONFIG.copy()
        
        if args.name:
            config['name'] = args.name
        
        if args.gpu:
            config['gpu'] = args.gpu
            
        if args.disk:
            config['diskInGb'] = args.disk
            config['containerDiskInGb'] = args.disk
        
        # Create the pod with fallback
        pod_id = manager.create_pod(config)
        
        if pod_id:
            print(f"Pod successfully created: {pod_id}")
            
            # Wait for the pod to be ready
            pod_data = manager.wait_for_pod_ready(pod_id)
            
            if pod_data:
                print(f"Pod {pod_id} is ready and running!")
                print(f"To connect via SSH, use: python3 runpod_manager.py connect")
                print(f"To backup critical files, please check the README.md")
            else:
                print("Pod didn't reach 'ready' state.")
        else:
            print("Pod creation failed. No resources available at the moment.")
    
    elif args.command == 'start':
        # Get the pod ID from environment
        pod_id = load_pod_id()
        
        if pod_id:
            print(f"Starting pod {pod_id}...")
            status, pod_data = manager.get_pod_status(pod_id)
            
            if status == "RUNNING":
                print(f"Pod {pod_id} is already running.")
            else:
                print(f"Pod {pod_id} is currently {status}. Starting it...")
                print("⚠️ IMPORTANT: The SSH port will likely change after restart.")
                print("The script will automatically detect the new port and update the configuration.")
                
                if manager.start_pod_cli(pod_id):
                    print(f"✅ Pod {pod_id} started successfully")
                    print("The startup process includes automatic file restoration from backup")
                    print("If the automatic restoration failed, you can run the restore command manually:")
                    print("    python3 runpod_manager.py restore")
                else:
                    print(f"❌ Failed to start pod {pod_id}")
        else:
            print("❌ No pod ID found. Please create a pod first with:")
            print("    python3 runpod_manager.py create")
    
    elif args.command == 'stop':
        # Get the pod ID from environment
        pod_id = load_pod_id()
        
        if pod_id:
            if manager.stop_pod(pod_id):
                print(f"Pod {pod_id} stopped successfully")
            else:
                print(f"Failed to stop pod {pod_id}")
        else:
            print("No pod ID found. Please create a pod first.")
    
    elif args.command == 'terminate':
        # Get the pod ID from environment
        pod_id = load_pod_id()
        
        if pod_id:
            # Display a message about manual backup
            print("⚠️ IMPORTANT: Don't forget to manually backup your data before terminating the pod!")
            print("   Consult the README.md for manual backup instructions.")
            
            # Then terminate the pod
            if manager.terminate_pod(pod_id):
                print(f"Pod {pod_id} terminated successfully")
                # Clear the pod ID from environment
                save_pod_id_env("", None, None, None)
            else:
                print(f"Failed to terminate pod {pod_id}")
        else:
            print("No pod ID found. Please create a pod first.")
    
    elif args.command == 'list':
        # List pods
        pods = manager.list_pods()
        if pods:
            print(f"Found {len(pods)} pod(s):")
            for pod in pods:
                pod_id = pod.get("id", "UNKNOWN")
                status = pod.get("status", pod.get("desiredStatus", "UNKNOWN"))
                gpu_type = pod.get("gpuDisplayName", pod.get("machine", {}).get("gpuDisplayName", "UNKNOWN"))
                print(f"  ID: {pod_id}, Status: {status}, GPU: {gpu_type}")
                
                # If it's the first pod and has a valid ID, save it to .env
                if pod_id != "UNKNOWN" and pods.index(pod) == 0:
                    saved_pod_id = load_pod_id()
                    # Save ID only if it's different from the already saved one
                    if saved_pod_id != pod_id:
                        print(f"✅ Saving pod ID {pod_id} to .env file")
                        save_pod_id_env(pod_id)
                    
                    # If the pod is running, offer to configure SSH
                    if status == "RUNNING":
                        ssh_host = os.getenv("SSH_HOST")
                        ssh_port = os.getenv("SSH_PORT")
                        
                        if not ssh_host or not ssh_port:
                            configure_ssh = input("Do you want to configure SSH connection for this pod? (y/n): ")
                            if configure_ssh.lower() in ["y", "yes"]:
                                print("Configuring SSH connection...")
                                ssh_info = manager.connect(pod)
                                if ssh_info:
                                    print("✅ SSH connection successfully configured")
                                else:
                                    print("❌ Failed to configure SSH connection")
        else:
            print("No pods found.")
            
            # Offer to create a pod
            create_pod = input("Do you want to create a new pod? (y/n): ")
            if create_pod.lower() in ["y", "yes"]:
                print("To create a pod, run: python runpod_manager.py create")
    
    elif args.command == 'backup':
        # Get pod ID
        pod_id = load_pod_id()
        
        if not pod_id:
            print("❌ No pod ID found. Please first create a pod or list it with 'python runpod_manager.py list'")
            sys.exit(1)
            
        # Check if SSH variables are defined
        ssh_host = os.getenv("SSH_HOST")
        ssh_port = os.getenv("SSH_PORT")
        
        # If SSH variables are not defined, use connect to configure them
        if not ssh_host or not ssh_port:
            print("ℹ️ Missing SSH variables, attempting configuration...")
            ssh_info = manager.connect()
            if not ssh_info:
                print("❌ Unable to configure SSH connection. First run 'python runpod_manager.py connect'")
                sys.exit(1)
        
        # Create minimal pod_data object with ID
        pod_data = {"id": pod_id}
        
        # Execute backup
        print(f"Backing up Gensyn files from pod {pod_id}...")
        if manager.backup_gensyn_data(pod_data):
            print(f"✅ Successfully backed up Gensyn files from pod {pod_id}")
            print(f"Files are stored in {GENSYN_BACKUP_DIR}")
        else:
            print(f"❌ Failed to backup Gensyn files from pod {pod_id}")
    
    elif args.command == 'restore' or args.command == 'deploy':
        # Get pod ID
        pod_id = load_pod_id()
        
        if not pod_id:
            print("❌ No pod ID found. Please first create a pod or list it with 'python runpod_manager.py list'")
            sys.exit(1)
            
        # Check if SSH variables are defined
        ssh_host = os.getenv("SSH_HOST")
        ssh_port = os.getenv("SSH_PORT")
        
        # If SSH variables are not defined, use connect to configure them
        if not ssh_host or not ssh_port:
            print("ℹ️ Missing SSH variables, attempting configuration...")
            ssh_info = manager.connect()
            if not ssh_info:
                print("❌ Unable to configure SSH connection. First run 'python runpod_manager.py connect'")
                sys.exit(1)
        
        # Check pod status
        status, pod_data = manager.get_pod_status(pod_id)
        
        if not pod_data:
            # If get_pod_status fails, create minimal pod_data object
            pod_data = {"id": pod_id}
        
        if status and status not in ["RUNNING", "READY"]:
            print(f"⚠️ Pod {pod_id} is not running (status: {status}).")
            
            # Start the pod
            print("Starting the pod...")
            if manager.start_pod(pod_id):
                print(f"Pod {pod_id} started successfully")
                # Wait for SSH to be available
                print("Waiting 30 seconds for SSH connection to be available...")
                time.sleep(30)
            else:
                print(f"❌ Failed to start pod {pod_id}")
                sys.exit(1)
        
        # Restore the data
        print(f"Restoring Gensyn files to pod {pod_id}...")
        if manager.restore_gensyn(pod_data):
            print(f"✅ Gensyn files successfully restored to pod {pod_id}")
        else:
            print(f"❌ Failed to restore Gensyn files to pod {pod_id}")
            print(f"Make sure backup files exist in {GENSYN_BACKUP_DIR}")

    elif args.command == 'ssh' or args.command == 'connect':
        # Use the improved connect function
        manager = RunPodManager(API_KEY)
        ssh_info = manager.connect()
        
        # Note: les messages d'erreur sont maintenant dans la fonction connect
        # Nous n'avons pas besoin de vérifier ssh_info ici car la fonction connect
        # gère déjà l'affichage des messages d'erreur appropriés

    elif args.command == 'clean':
        clean_pod_info()
    
    else:
        # If no command is provided, show help
        parser.print_help()

if __name__ == "__main__":
    sys.exit(main()) 