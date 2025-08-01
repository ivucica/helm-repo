#!/usr/bin/env python3
# run inside of the cloned repo.

import os
import subprocess
import yaml
import requests
from typing import List, Dict

def find_chart_directories(root_path: str) -> List[str]:
    """Finds all directories containing a Chart.yaml file."""
    chart_dirs = []
    print(f"Scanning for charts in: {root_path}")
    if not os.path.isdir(root_path):
        print(f"Warning: Directory not found, skipping: {root_path}")
        return []

    for dirpath, _, filenames in os.walk(root_path):
        if "Chart.yaml" in filenames:
            if os.path.basename(dirpath) == 'common' and 'library' in dirpath:
                print(f"Skipping library chart: {dirpath}")
                continue
            chart_dirs.append(dirpath)
    return chart_dirs
    
def get_chart_info(chart_dir: str) -> Dict[str, str] or None:
    """Parses Chart.yaml to get name and version."""
    chart_yaml_path = os.path.join(chart_dir, "Chart.yaml")
    try:
        with open(chart_yaml_path, 'r', encoding='utf-8') as f:
            chart_data = yaml.safe_load(f)
        return {"name": chart_data.get("name"), "version": chart_data.get("version")}
    except (IOError, yaml.YAMLError) as e:
        print(f"Error reading or parsing {chart_yaml_path}: {e}")
        return None

def run_command(command: list, suppress_output: bool = False, suppress_error: bool = False) -> bool:
    """Runs a shell command and returns True on success."""
    try:
        process = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=True,
            encoding='utf-8'
        )
        if process.stdout and not suppress_output:
            print(process.stdout)
        return True
    except FileNotFoundError:
        print(f"Error: The command '{command[0]}' was not found.")
        print("Please ensure the Helm CLI is installed and in your system's PATH.")
        return False
    except subprocess.CalledProcessError as e:
        if not suppress_error:
            print(f"Error executing command: {' '.join(command)}")
            print(f"Stderr: {e.stderr}")
        return False

def create_helm_index(repo_path: str, repo_url: str):
    """
    Creates a Helm repository index.yaml file by finding, packaging,
    and indexing charts.
    """
    charts_root = os.path.join(repo_path, "charts")
    package_dir = os.path.join(repo_path, "helm-repo")
    os.makedirs(package_dir, exist_ok=True)
    print(f"Chart packages will be placed in: {package_dir}\\n")

    # --- Step 1: Fetch existing index from GitHub Pages ---
    processed_charts = {}
    if repo_url:
        index_url = f"{repo_url.rstrip('/')}/index.yaml"
        print(f"--- 1. Fetching existing index from: {index_url} ---")
        try:
            response = requests.get(index_url)
            response.raise_for_status()
            remote_index = yaml.safe_load(response.text)
            if remote_index and 'entries' in remote_index:
                for chart_name, entries in remote_index.get("entries", {}).items():
                    for entry_details in entries:
                        version = entry_details.get("version")
                        if version:
                            processed_charts[(chart_name, version)] = entry_details
                print(f"Found {len(processed_charts)} existing chart versions in the remote index.")
        except requests.exceptions.RequestException as e:
            print(f"Could not fetch remote index (this is normal on first run): {e}")
        except yaml.YAMLError as e:
            print(f"Could not parse remote index.yaml: {e}")
    else:
        print("--- 1. REPO_HOST_URL not set, skipping remote index fetch. ---")

    # --- Step 2: Scan for local charts ---
    print("\n--- 2. Scanning for local charts ---")
    chart_subdirs = ["stable", "premium", "incubator", "system", "library"]
    all_chart_dirs = []
    for subdir in chart_subdirs:
        path = os.path.join(charts_root, subdir)
        found = find_chart_directories(path)
        print(f"- Found {len(found)} charts in '{subdir}'")
        all_chart_dirs.extend(found)
    if not all_chart_dirs:
        print("\nNo charts found. Exiting.")
        return

    # --- Step 3: Process all charts ---
    total_charts = len(all_chart_dirs)
    print(f"\n--- 3. Processing {total_charts} total charts ---")
    for i, chart_dir in enumerate(all_chart_dirs, 1):
        info = get_chart_info(chart_dir)
        if not info or not info.get("name") or not info.get("version"):
            print(f" -> Skipping directory {chart_dir} due to missing chart info.")
            continue
        
        chart_name, chart_version = info["name"], info["version"]
        package_filename = f"{chart_name}-{chart_version}.tgz"
        package_path = os.path.join(package_dir, package_filename)

        if os.path.exists(package_path):
            continue

        current_time = datetime.datetime.now().strftime('%H:%M:%S')
        print(f"[{i}/{total_charts}] {current_time} - Processing: {chart_name} v{chart_version}")

        # Strategy 1: Download from existing GitHub Pages repo
        if (chart_name, chart_version) in processed_charts:
            entry = processed_charts[(chart_name, chart_version)]
            if entry.get("urls") and entry["urls"][0]:
                chart_url = f"{repo_url.rstrip('/')}/{entry['urls'][0]}"
                print(f"   - Found in remote index. Downloading from {chart_url}...")
                try:
                    res = requests.get(chart_url, stream=True)
                    res.raise_for_status()
                    with open(package_path, 'wb') as f:
                        for chunk in res.iter_content(chunk_size=8192): f.write(chunk)
                    print(f"   -> SUCCESS: Downloaded pre-existing package.")
                    continue
                except requests.exceptions.RequestException as e:
                    print(f"   -> FAILED to download from remote index: {e}. Will try other methods.")

        # Strategy 2: Pull from OCI registry
        print(f"   - Not in remote index. Trying to pull from oci://quay.io/truecharts/{chart_name}...")
        oci_pull_command = ["helm", "pull", f"oci://quay.io/truecharts/{chart_name}", "--version", chart_version, "--destination", package_dir]
        if run_command(oci_pull_command, suppress_error=True, suppress_output=True):
            print(f"   -> SUCCESS: Pulled from OCI.")
            if not os.path.exists(package_path):
                 print(f"   -> WARNING: Helm pull reported success, but package not found. Building from source.")
            else:
                 continue
        else:
            print(f"   - OCI pull failed. Will build from source.")

        # Strategy 3: Build from source
        print(f"   - Building from source: {chart_dir}")
        print(f"     - Building dependencies...")
        if not run_command(["helm", "dependency", "build", chart_dir], suppress_output=True, suppress_error=True):
            print(f"   -> FAILED to build dependencies for {chart_name}. Skipping.")
            continue
        print(f"     - Packaging chart...")
        if not run_command(["helm", "package", chart_dir, "--destination", package_dir]):
            print(f"   -> FAILED to package {chart_name}. Skipping.")
            continue
        print(f"   -> SUCCESS: Built from source.")

    # --- Step 4. Generate final index ---
    print(f"\n--- 4. Generating final index.yaml ---")
    print(f"Indexing all packages in '{package_dir}' with URL '{repo_url}'")
    run_command(["helm", "index", package_dir, "--url", repo_url])
    print(f"\nSuccessfully generated index.yaml.")

    print("\\n--- Repository Ready ---")
    print(f"The '{os.path.basename(package_dir)}' directory is ready for deployment.")
    print(f"1. Upload the entire '{os.path.basename(package_dir)}' directory to a web server.")
    print(f"2. The server must make the files available at the URL you provided: {repo_url}")
    print(f"3. Add the repository using the Helm client:")
    print(f"   helm repo add my-charts {repo_url}")
    print("   helm repo update")
    print("--------------------------")


if __name__ == "__main__":
    # --- Configuration ---
    # The absolute URL where your packaged charts will be hosted.
    # IMPORTANT: You MUST change this to your own URL or set the env var.
    REPO_HOST_URL = os.getenv("REPO_HOST_URL", "https://your-server.com/path-to-charts")

    # The local path to the cloned repository.
    # This script assumes it is located in the root of the repository.
    REPO_LOCAL_PATH = os.getenv("REPO_LOCAL_PATH", ".")

    create_helm_index(REPO_LOCAL_PATH, REPO_HOST_URL)
