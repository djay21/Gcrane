# gcrane

# Script Update with Service Account

This repository contains scripts and configurations to facilitate authentication and operations with Azure and Google Cloud services using service accounts. Follow the steps below to ensure the setup and proper functioning of these scripts.

## Prerequisites

Before starting, ensure you have the following tools installed:

### Install Python 3.11

```bash
sudo add-apt-repository ppa:deadsnakes/ppa 
sudo apt update
sudo apt install python3.11 -y
```

### Install Docker
sudo apt install docker.io

### Install Google Cloud SDK
curl https://sdk.cloud.google.com > install.sh
bash install.sh --disable-prompts

### Install Azure CLI
curl -sL https://aka.ms/InstallAzureCLIDeb | sudo bash

### Install gcrane

curl -L https://github.com/google/go-containerregistry/releases/latest/download/go-containerregistry_Linux_x86_64.tar.gz -o go-containerregistry.tar.gz
tar -zxvf go-containerregistry.tar.gz
chmod +x gcrane
sudo mv gcrane /usr/local/bin/

Step 1: Set Up Azure Service Account
Create Service Principal on Azure
Run the following command in Azure CLI to create a service principal:

az ad sp create-for-rbac --name "<service-principal-name>" --role contributor --scopes /subscriptions/<your-subscription-id> --sdk-auth
Save the output JSON in a secure location (e.g., .azure_credentials.json).

Assign Necessary Permissions
Ensure the service principal has permissions to access Azure Container Registry (ACR):

az role assignment create --assignee <service-principal-id> --role AcrPush --scope /subscriptions/<subscription-id>/resourceGroups/<resource-group>/providers/Microsoft.ContainerRegistry/registries/<acr-name>

Configure Environment Variables for Azure
Store credentials in your .env file:

AZURE_CLIENT_ID=<your-client-id>
AZURE_TENANT_ID=<your-tenant-id>
AZURE_CLIENT_SECRET=<your-client-secret>
AZURE_SUBSCRIPTION_ID=<your-subscription-id>

Export these credentials:
export $(cat .env | xargs)
Step 2: Set Up Google Cloud Service Account
Create Service Account on Google Cloud
Create a service account:

gcloud iam service-accounts create <service-account-name> --display-name="Replication Service Account"

Assign Artifact Registry Permissions
Attach necessary roles:


gcloud projects add-iam-policy-binding <project-id> \
  --member="serviceAccount:<service-account-name>@<project-id>.iam.gserviceaccount.com" \
  --role="roles/artifactregistry.admin"

### Generate a Key for the Service Account
Generate a key file in JSON format:
gcloud iam service-accounts keys create ~/gcp-key.json --iam-account <service-account-name>@<project-id>.iam.gserviceaccount.com

Configure Environment Variables for Google Cloud
Add to your script:

#!/bin/bash 

export GOOGLE_APPLICATION_CREDENTIALS="/path/to/your/gcp-key.json"

gcloud auth activate-service-account acr-gar@dummy-632783.iam.gserviceaccount.com --key-file="/path/to/your/gcp-key.json"


### Step 3: Install Required Tools and SDKs
Install Python Packages
Create a requirements.txt file with the following content:
azure-identity 
azure-mgmt-containerregistry
python-dotenv
google-cloud-storage
google-auth
tqdm
Install packages using:
pip3 install -r requirements.txt
