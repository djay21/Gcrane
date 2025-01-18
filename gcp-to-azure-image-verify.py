import subprocess
import json
import os
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from tqdm import tqdm




acr_output_file = "acr_all_images.txt"
gcr_output_file = 'gar_all_images.txt'
difference_output_file = "difference_images.txt"
destination_registry = "us-docker.pkg.dev"
location = "us"
project_id = "apac-inmobi-internal"




def remove_old_files():
  if os.path.exists(acr_output_file):
      os.remove(acr_output_file)
      print(f"Removed old file: {acr_output_file}")
  if os.path.exists(gcr_output_file):
      os.remove(gcr_output_file)
      print(f"Removed old file: {gcr_output_file}")
  if os.path.exists(difference_output_file):
      os.remove(difference_output_file)
      print(f"Removed old file: {difference_output_file}")




########################## GCP GAR ##########################




def get_all_artifact_registries():
  try:
      cmd = "gcloud artifacts repositories list --format=json"
      proc = subprocess.run(cmd, shell=True, capture_output=True, text=True, check=True)
      output = proc.stdout.strip()
      return json.loads(output)
  except subprocess.CalledProcessError as e:
      print("Error listing Artifact Registries:")
      print(e.stderr)
      return []




def get_tags_for_digest(location, project_id, repository_id, image_name, digest):
  try:
      repository_path = f"{location}-docker.pkg.dev/{project_id}/{repository_id}/{image_name}"
      cmd_tags = f"gcloud container images list-tags {repository_path} --filter='digest:{digest}' --format='get(tags)'"
    
      proc_tags = subprocess.run(cmd_tags, shell=True, capture_output=True, text=True, check=True)
      tags_output = proc_tags.stdout.strip()
      tags = tags_output.split(';') if tags_output else []
      return tags
  except subprocess.CalledProcessError as e:
      print(f"Error retrieving tags for digest '{digest}' in image '{image_name}':")
      print(e.stderr)
      return []




def list_docker_images_for_repository(location, project_id, repository_id):
  try:
      image_list = []
      repository_path = f"{location}-docker.pkg.dev/{project_id}/{repository_id}"
      cmd_images = f"gcloud artifacts docker images list {repository_path} --format=json"




      proc_images = subprocess.run(cmd_images, shell=True, capture_output=True, text=True, check=True)
      output = proc_images.stdout.strip()
      images = json.loads(output)




      for image in images:
          package_path = image.get('package', '')
          digest = image.get('version', '')  # Getting the version which corresponds to digest
          tags = image.get('tags', [])  # Get the list of tags




          image_name = package_path.split('/')[-1]




          if not tags:
              tags = get_tags_for_digest(location, project_id, repository_id, image_name, digest)




          if tags:
              for tag in tags:
                  formatted_image = f"{repository_id}/{image_name}:{tag}:-{digest}"
                  image_list.append(formatted_image)
          else:
              formatted_image = f"{repository_id}:{image_name}:<no-tag>:-{digest}"
              image_list.append(formatted_image)




      return image_list
  except subprocess.CalledProcessError as e:
      print(f"Error listing images for repository '{repository_id}':")
      print(e.stderr)
      return []




def write_images_to_file(image_list, filename):
  try:
      with open(filename, 'w') as file:
          for image in image_list:
              file.write(f"{image}\n")
      print(f"Image list has been written to {filename}")
  except IOError as e:
      print(f"Failed to write to the file: {filename}")
      print(e)




def get_gar():
  registries = get_all_artifact_registries()
  all_images = []




  def process_registry(registry):
      location = registry.get('name').split('/')[3]
      project_id = registry.get('name').split('/')[1]
      repository_id = registry.get('name').split('/')[-1]
      return list_docker_images_for_repository(location, project_id, repository_id)




  with ThreadPoolExecutor() as executor:
      results = list(tqdm(executor.map(process_registry, registries), total=len(registries), desc="Fetching GCP Images"))
      for images in results:
          all_images.extend(images)
   write_images_to_file(all_images, gcr_output_file)




######################################## AZURE ########




def get_all_acr_names():
  try:
      cmd = "az acr list --query '[].name' --output json"
      proc = subprocess.run(cmd, shell=True, capture_output=True, text=True, check=True)
      return json.loads(proc.stdout)
  except subprocess.CalledProcessError as e:
      print("Error listing Azure Container Registries:")
      print(e.stderr)
      return []




def list_acr_images_with_digests(registry_name):
  try:
      cmd_repos = f"az acr repository list --name {registry_name} --output json"
      proc_repos = subprocess.run(cmd_repos, shell=True, capture_output=True, text=True, check=True)
      repositories = json.loads(proc_repos.stdout)
    
      image_list = []




      for repo in repositories:
          cmd_tags = f"az acr repository show-tags --name {registry_name} --repository {repo} --output json"
          proc_tags = subprocess.run(cmd_tags, shell=True, capture_output=True, text=True, check=True)
          tags = json.loads(proc_tags.stdout)




          for tag in tags:
              cmd_manifest = f"az acr repository show-manifests --name {registry_name} --repository {repo} --query \"[?tags && contains(@.tags, '{tag}')].digest\" --output json"
              proc_manifest = subprocess.run(cmd_manifest, shell=True, capture_output=True, text=True, check=True)
              digests = json.loads(proc_manifest.stdout)




              if digests:
                  digest = digests[0]
                  image_identifier = f"{registry_name}:{repo}:{tag}:-{digest}"
                  image_list.append(image_identifier)




      return image_list
  except subprocess.CalledProcessError as e:
      print(f"Error accessing registry '{registry_name}':")
      print(e.stderr)
      return []




def get_acr():
  acr_names = get_all_acr_names()
  all_images = []




  def process_registry(registry_name):
      return list_acr_images_with_digests(registry_name)




  with ThreadPoolExecutor() as executor:
      results = list(tqdm(executor.map(process_registry, acr_names), total=len(acr_names), desc="Fetching ACR Images"))
      for images in results:
          all_images.extend(images)




  write_images_to_file(all_images, acr_output_file)




########################## Comparison and File I/O ##########################




def read_images_from_file(filename):
  try:
      with open(filename, 'r') as file:
          return {line.strip() for line in file}
  except IOError as e:
      print(f"Failed to read the file: {filename}")
      print(e)
      return set()


def read_images_from_file(file_path):
  with open(file_path) as f:
      images = set(line.strip() for line in f.readlines())
  return images




def compare_registries(acr_file, gcr_file, difference_file):
  acr_images = read_images_from_file(acr_file)
  gcr_images = read_images_from_file(gcr_file)
  missing_in_acr = acr_images - gcr_images
  if missing_in_acr:
      print("Images missing in Google Container Registry:")
      with open(difference_file, 'w') as f:
          for image in missing_in_acr:
              print(image)
              f.write(image + '\n')
          return True
  else:
      print("No images are missing from Azure Container Registry compared to Google Artifact Registry.")
      if os.path.exists(difference_output_file):
          os.remove(difference_output_file)  # Clear the file if it exists
      return False  # No difference file created




    
if __name__ == "__main__":
  remove_old_files()
  get_acr()
  get_gar()
  difference_created = compare_registries(acr_output_file, gcr_output_file, difference_output_file)
  # Only run the copy function if the difference file was created and is non-empty
  if difference_created and os.path.getsize(difference_output_file) > 0:
      print("\n**** Difference in ACR to GAR Found. Content stored in", difference_output_file)
  else:
      print("No images to copy. The difference file was not created or is empty.")
       