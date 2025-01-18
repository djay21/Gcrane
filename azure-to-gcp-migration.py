import logging
import sys
import argparse
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from azure.identity import DefaultAzureCredential
from azure.mgmt.containerregistry import ContainerRegistryManagementClient
import time
import os
import sqlite3
from dotenv import load_dotenv




# Load environment variables from .env file
load_dotenv()




# Set up logging (INFO level to reduce verbosity)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


# Suppress specific logging from Azure SDK
logging.getLogger("azure.core.pipeline.policies.http_logging_policy").setLevel(logging.WARNING)
logging.getLogger("azure.identity").setLevel(logging.WARNING)


# Constants
GCR_PROJECT_ID = os.getenv("GCR_PROJECT_ID")
GCR_REGION = os.getenv("GCR_REGION")
MAX_CONCURRENT_JOBS = int(os.getenv("MAX_CONCURRENT_JOBS", 5))  # Default to 5 if not provided
RETRY_LIMIT = int(os.getenv("RETRY_LIMIT", 3))  # Default to 3 if not provided


DB_FILE = "processed.db"


def init_db():
   """Initialize the SQLite database and create the necessary table."""
   conn = sqlite3.connect(DB_FILE)
   cursor = conn.cursor()


   # Create table if it doesn't exist
   cursor.execute('''
       CREATE TABLE IF NOT EXISTS processed_entries (
           acr_name TEXT,
           repository TEXT,
           tag TEXT,
           digest TEXT,
           PRIMARY KEY (acr_name, repository, tag, digest)
       )
   ''')


   conn.commit()
   conn.close()


def insert_processed(acr_name, repo, tag, digest):
   """Insert a processed entry into the SQLite database."""
   conn = sqlite3.connect(DB_FILE)
   cursor = conn.cursor()


   cursor.execute('''
       INSERT OR IGNORE INTO processed_entries (acr_name, repository, tag, digest)
       VALUES (?, ?, ?, ?)
   ''', (acr_name, repo, tag, digest))


   conn.commit()
   conn.close()


def check_if_processed(acr_name, repo, tag, digest):
   """Check if an entry exists in the processed table."""
   conn = sqlite3.connect(DB_FILE)
   cursor = conn.cursor()


   cursor.execute('''
       SELECT EXISTS(
           SELECT 1 FROM processed_entries
           WHERE acr_name = ? AND repository = ? AND tag = ? AND digest = ?
       )
   ''', (acr_name, repo, tag, digest))


   result = cursor.fetchone()[0]
   conn.close()


   return result == 1


def get_all_processed():
   """Retrieve all processed entries from the database."""
   conn = sqlite3.connect(DB_FILE)
   cursor = conn.cursor()


   cursor.execute('SELECT * FROM processed_entries')
   rows = cursor.fetchall()
  
   conn.close()
   return rows


def clear_all_processed():
   """Clear all processed entries from the database."""
   conn = sqlite3.connect(DB_FILE)
   cursor = conn.cursor()


   cursor.execute('DELETE FROM processed_entries')


   conn.commit()
   conn.close()




# Function to get the default Azure subscription ID using Azure CLI
def get_azure_subscription_id():
   try:
       subscription_id = subprocess.check_output(
           ["az", "account", "list", "--query", "[?isDefault].id", "-o", "tsv"]
       ).decode().strip()
       return subscription_id
   except subprocess.CalledProcessError as e:
       logging.error(f"Failed to fetch the Azure subscription ID: {e}")
       return None


# Retry decorator for functions
def retry(retries=RETRY_LIMIT, delay=2):
   def decorator(func):
       def wrapper(*args, **kwargs):
           for i in range(retries):
               try:
                   return func(*args, **kwargs)
               except Exception as e:
                   logging.warning(f"Retrying due to: {e}, attempt {i+1}/{retries}")
                   time.sleep(delay)
           logging.error(f"Function {func.__name__} failed after {retries} retries")
       return wrapper
   return decorator


# Azure setup
subscription_id = get_azure_subscription_id()
if not subscription_id:
   exit(1)  # Exit if the subscription ID couldn't be retrieved


credential = DefaultAzureCredential()
client = ContainerRegistryManagementClient(credential, subscription_id)


@retry()
def list_acr_registries():
   return [registry.name for registry in client.registries.list()]


@retry()
def get_acr_credentials(acr_name, resource_group_name):
   try:
       credentials = client.registries.list_credentials(resource_group_name, acr_name)
       return credentials.username, credentials.passwords[0].value
   except Exception as e:
       logging.error(f"Error getting credentials for {acr_name} in resource group {resource_group_name}: {e}")
       raise


@retry()
def get_resource_group_name(acr_name):
   try:
       resource_group = subprocess.check_output(
           ["az", "acr", "show", "--name", acr_name, "--query", "resourceGroup", "--output", "tsv"]
       ).decode().strip()
       return resource_group
   except subprocess.CalledProcessError as e:
       logging.error(f"Failed to fetch the resource group name for ACR {acr_name}: {e}")
       return None


@retry()
def list_repositories(acr_name):
   return subprocess.check_output(["az", "acr", "repository", "list", "--name", acr_name, "--output", "tsv"]).decode().split()


@retry()
def list_tags(acr_name, repo):
   return subprocess.check_output(["az", "acr", "repository", "show-tags", "--name", acr_name, "--repository", repo, "--output", "tsv"]).decode().split()


@retry()
def get_tag_digest(acr_name, repo, tag):
   """Retrieve the digest for a specific tag in a repository."""
   try:
       digest = subprocess.check_output(
           ["az", "acr", "manifest", "list-metadata", "-r", acr_name, "-n", repo, "--query", f"[?tags[?contains(@, '{tag}')]].digest", "--output", "tsv" , "--only-show-errors"]
       ).decode().strip()
       return digest
   except subprocess.CalledProcessError as e:
       logging.error(f"Failed to get digest for {acr_name}/{repo}:{tag}: {e}")
       return None


@retry()
def create_gcr_repository(repo_name):
   """Try to create GCR repository and handle the 'already exists' error directly."""
   command = [
       "gcloud", "artifacts", "repositories", "create", repo_name,
       "--repository-format=docker", "--location=us",
       "--description", f"Repository for {repo_name}"
   ]
   result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


   if result.returncode == 0:
       logging.info(f"Successfully created repository {repo_name}")
   elif "ALREADY_EXISTS" in result.stderr.decode():
       logging.info(f"Repository {repo_name} already exists. Skipping creation.")
   else:
       logging.error(f"Failed to create repository {repo_name}: {result.stderr.decode()}")


@retry()
def copy_image(source_image, dest_image):
   command = f"gcrane cp {source_image} {dest_image}"
   result = subprocess.run(command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
   if result.returncode == 0:
       logging.info(f"Successfully copied {source_image} to {dest_image}")
   else:
       logging.error(f"Failed to copy {source_image} to {dest_image}: {result.stderr.decode()}")


def load_processed():
   """Load processed registries and repositories with tags and digests from file."""
   if os.path.exists(PROCESSED_FILE):
       with open(PROCESSED_FILE, 'r') as f:
           return set(line.strip() for line in f)
   return set()


def save_processed(acr_name, repo, tag, digest):
   """Save processed ACR, repository, tag, and digest to file."""
   with open(PROCESSED_FILE, 'a') as f:
       f.write(f"{acr_name}/{repo}:{tag}:{digest}\n")


def process_repository(acr_name, repo):
   try:
       tags = list_tags(acr_name, repo)
       dest_repo_name = f"{acr_name}"
       create_gcr_repository(dest_repo_name)


       for tag in tags:
           digest = get_tag_digest(acr_name, repo, tag)
           if not digest:
               logging.error(f"Skipping {acr_name}/{repo}:{tag} due to missing digest.")
               continue


           # Check if this repository:tag:digest has been processed before using SQLite
           if check_if_processed(acr_name, repo, tag, digest):
               logging.info(f"Skipping {acr_name}/{repo}:{tag}:{digest} as it has been processed before.")
               continue


           with ThreadPoolExecutor(max_workers=MAX_CONCURRENT_JOBS) as executor:
               futures = [
                   executor.submit(copy_image, f"{acr_name}.azurecr.io/{repo}:{tag}", f"{GCR_REGION}-docker.pkg.dev/{GCR_PROJECT_ID}/{acr_name}/{repo}:{tag}")
               ]
               for future in as_completed(futures):
                   future.result()  # This will raise any exceptions caught during image copy


           # After successfully processing the repository:tag:digest, save it in SQLite
           insert_processed(acr_name, repo, tag, digest)
   except Exception as exc:
       logging.error(f"Error processing repository {repo}: {exc}")


def process_acr(acr_name):
   resource_group_name = get_resource_group_name(acr_name)
   if not resource_group_name:
       logging.error(f"Skipping ACR {acr_name} due to missing resource group name")
       return


   repositories = list_repositories(acr_name)
   #print("repsostiroes ares:", repositories)
   for repo in repositories:
       # print( "*****************repo is ", repo)
       # print("********** processed files are" ,processed)
       process_repository(acr_name, repo)


def chunkify(lst, n):
   return [lst[i:i + n] for i in range(0, len(lst), n)]


def ensure_gar_repository(project_id, repository_name):
  try:
      # Check if the repository exists
      result = subprocess.run(
          ["gcloud", "artifacts", "repositories", "list",
           "--project", project_id, "--location", GCR_REGION,
           "--filter", f"name:projects/{project_id}/locations/{GCR_REGION}/repositories/{repository_name}",
           "--format", "value(name)"], capture_output=True, text=True, check=True
      )
      # If repository doesn't exist, it creates it
      if not result.stdout.strip():
          print(f"Creating repository {repository_name} in project {project_id}.")
          subprocess.run(
              ["gcloud", "artifacts", "repositories", "create", repository_name,
               "--repository-format=docker",
               "--location", location,
               "--project", project_id],
              check=True
          )
          print(f"Repository {repository_name} created.")
      else:
          print(f"Repository {repository_name} already exists.")
  except subprocess.CalledProcessError as e:
      print(f"An error occurred while checking or creating the repository: {e}")

def copy_images_to_gcr(difference_file):
   try:
       with open(difference_file, 'r') as f:
           original_images = f.readlines()
       if not original_images:
           print("No images to copy.")
           return
        with ThreadPoolExecutor() as executor:
           futures = []
           for original_image in original_images:
               original_image = original_image.strip()
               parts = original_image.split(':')
               if len(parts) >= 3:
                   repo_name = parts[0]
                   ensure_gar_repository(GCR_PROJECT_ID, repo_name)
                   azure_registry = f"{parts[0]}.azurecr.io"
                   image_path_with_tag = f"{parts[1]}:{parts[2]}"
                   source_image = f"{azure_registry}/{image_path_with_tag}"
                   destination_image = f"{GCR_REGION}-docker.pkg.dev/{GCR_PROJECT_ID}/{parts[0]}/{image_path_with_tag}"
                 
                   futures.append(executor.submit(copy_image, source_image, destination_image))


      
           for future in as_completed(futures):
               try:
                   future.result()
               except Exception as e:
                   print(f"An error occurred during image copy: {e}")


       Image = original_image.split(':')
       if len(Image) >= 3:
                   acr_name = Image[0]
                   repo = Image[1]
                   tag  = Image[2]
                   digest =  f"{parts[3]}:{parts[4]}"
                   insert_processed(acr_name, repo, tag, digest)
       processed_entries = get_all_processed()
       for entry in processed_entries:
           print(f"Processed: {entry}")
   except Exception as e:
       print(f"An error occurred: {e}")




if __name__ == "__main__":
   parser = argparse.ArgumentParser(description="A script that behaves differently based on command line arguments.")
   parser.add_argument('--diff-file', type=str, help='Path to a diff file.')


   args = parser.parse_args()


   if args.diff_file:
       if os.path.isfile(args.diff_file):
           copy_images_to_gcr(args.diff_file)
       else:
           print(f"Error: The path provided is not a valid file: {args.diff_file}")
           sys.exit(1)
   else:
       init_db()
       acr_names = list_acr_registries()
       chunks = chunkify(acr_names, MAX_CONCURRENT_JOBS)


       for chunk in chunks:
           with ThreadPoolExecutor(max_workers=MAX_CONCURRENT_JOBS) as executor:
               futures = [executor.submit(process_acr, acr_name) for acr_name in chunk]
               for future in as_completed(futures):
                   try:
                       future.result()
                   except Exception as exc:
                       logging.error(f"Error processing ACR: {exc}")


       logging.info("Finished processing all ACRs")


       # Print all processed entries from the database
       processed_entries = get_all_processed()
       for entry in processed_entries:
           print(f"Processed: {entry}")

#.env
# GCR_PROJECT_ID=abc-2
# GCR_REGION=us
# MAX_CONCURRENT_JOBS=5
# RETRY_LIMIT=3
# PROCESSED_FILE=processed.txt


# run commands
# python acr-gar.py


# #Also for Specific registries 
# python3 acr-gar.py –diff-file diff.txt
