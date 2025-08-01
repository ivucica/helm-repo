#!/usr/bin/env python3
# run inside of the cloned repo.

import os
import subprocess
import yaml
import requests
import datetime
import http.server
import socketserver
import threading
import functools
from typing import List, Dict

# --- Configuration ---
# Max number of charts to build from source in a single run to prevent timeouts.
MAX_SOURCE_BUILDS = 50
# Max number of downloads from OCI that are permitted. (The rest will eventually top up due to --merge.)
MAX_OCI_PULLS = 800
# Whether to add OCI link to repo.
ADD_OCI_LINK = False
# Whether to remove OCI link from repo.
REMOVE_OCI_LINK = True


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


def validate_index(package_dir: str):
    """
    Uses the helm binary to validate the generated index.yaml by treating it
    as a local repository. This ensures it is not malformed.
    """
    print("\n--- 6. Validating generated index.yaml ---")
    repo_name = "local-validation-repo"

    # Create a handler that serves files from the specified package directory
    Handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=package_dir)
    httpd = socketserver.TCPServer(("", 0), Handler)

    repo_port = httpd.server_address[1]
    repo_url = f"http://localhost:{repo_port}"

    # Start the server in a daemon thread so it exits when the main script does
    server_thread = threading.Thread(target=httpd.serve_forever)
    server_thread.daemon = True
    server_thread.start()

    try:
        print(f" - Started local web server for validation on port {repo_port}...")

        # Add the local directory as a temporary Helm repository
        print(f" - Adding temporary repo '{repo_name}' for validation...")
        if not run_command(["helm", "repo", "add", repo_name, repo_url]):
            print("\nFATAL: Failed to add local directory as a Helm repo for validation.")
            run_command(["cat", package_dir+"/index.yaml"], suppress_output=False)
            # TODO: make use of 'finally'
            httpd.shutdown()
            httpd.server_close()
            exit(1)

        # Try to search the repo. This will fail if the index is malformed.
        print(" - Searching repo to test index integrity...")
        if not run_command(["helm", "search", "repo", repo_name], suppress_output=True):
            print("\nFATAL: Helm failed to read the generated index.yaml. It is likely malformed.")
            run_command(["cat", package_dir+"/index.yaml"], supprtess_output=False)
            # TODO: make use of 'finally'
            run_command(["helm", "repo", "remove", repo_name], suppress_output=True) # Clean up
            httpd.shutdown()
            httpd.server_close()
            exit(1)

        print(" -> SUCCESS: index.yaml is valid.")

    finally:
        # Clean up by shutting down the server and removing the temporary repo
        print(" - Cleaning up...")
        httpd.shutdown()
        httpd.server_close()
        run_command(["helm", "repo", "remove", repo_name], suppress_output=True)


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

def post_process_index(index_path: str):
    """
    Opens the generated index.yaml, ensures it's clean UTF-8, and adds
    an OCI URL to each chart's 'urls' array if not already present.
    """
    print("\n--- 5. Post-processing index.yaml ---")
    if not os.path.exists(index_path):
        print(f"Warning: {index_path} not found. Skipping post-processing.")
        return

    try:
        with open(index_path, 'r', encoding='utf-8', errors='ignore') as f:
            index_data = yaml.safe_load(f)

        if not index_data or 'entries' not in index_data:
            print(f"Skipping postprocessing: {index_path} has no contents, or there is no key 'entries' in contents")
            return  # TODO: we skip processing, but we might submit empty yaml. do we want that?

        for chart_name, entries in index_data['entries'].items():
            for entry in entries:
                # Ensure appVersion is always a string to prevent parsing errors.
                if 'appVersion' in entry and entry['appVersion'] is not None:
                    if not isinstance(entry['appVersion'], str):
                        print(f"warning: appVersion {entry['appVersion']} is not a string in {str(entry)}")
                    entry['appVersion'] = str(entry['appVersion'])

                oci_url = f"oci://quay.io/truecharts/{chart_name}"
                # Ensure 'urls' key exists and is a list
                if 'urls' not in entry or not isinstance(entry['urls'], list):
                    entry['urls'] = []

                global ADD_OCI_URL, REMOVE_OCI_URL
                if ADD_OCI_URL:
                    # Add OCI URL if it's not already there
                    if oci_url not in entry['urls']:
                        entry['urls'].insert(0, oci_url) # Prioritize OCI URL
                if REMOVE_OCI_URL:
                    if oci_url in entry['urls']:
                        entry['urls'].remove(oci_url)

        with open(index_path, 'w', encoding='utf-8') as f:
            yaml.dump(index_data, f, default_flow_style=False)
        print(" -> SUCCESS: Post-processing complete. OCI URLs verified.")

    except (IOError, yaml.YAMLError) as e:
        print(f" -> FAILED to post-process index.yaml: {e}")

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
    existing_index = {}
    index_path = os.path.join(package_dir, "index.yaml")
    if repo_url:
        index_url = f"{repo_url.rstrip('/')}/index.yaml"
        print(f"--- 1. Fetching existing index from: {index_url} ---")
        try:
            response = requests.get(index_url)
            if response.status_code == 200:
                # Save the existing index to be merged later
                with open(index_path, 'wb') as f:
                    f.write(response.content)
                
                remote_index_text = response.content.decode('utf-8', errors='ignore')
                remote_index = yaml.safe_load(remote_index_text)

                if remote_index and 'entries' in remote_index:
                    existing_index = remote_index.get("entries", {})
                print(f"Found {len(existing_index)} unique charts in the remote index.")
            else:
                 print("Could not fetch remote index (this is normal on first run). Status code:", response.status_code)
        except requests.exceptions.RequestException as e:
            print(f"Could not fetch remote index: {e}")
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
    source_build_count = 0
    oci_pull_count = 0
    print(f"\n--- 3. Processing {total_charts} total charts ---")
    for i, chart_dir in enumerate(all_chart_dirs, 1):
        info = get_chart_info(chart_dir)
        if not info or not info.get("name") or not info.get("version"):
            print(f" -> Skipping directory {chart_dir} due to missing chart info.")
            continue

        chart_name, chart_version = info["name"], info["version"]
        
        # If chart version already exists in index, skip all processing.
        if chart_name in existing_index and any(v.get('version') == chart_version for v in existing_index[chart_name]):
            continue

        package_filename = f"{chart_name}-{chart_version}.tgz"
        package_path = os.path.join(package_dir, package_filename)

        # Also skip if the tarball already exists (e.g., from cache)
        if os.path.exists(package_path):
            continue

        current_time = datetime.datetime.now().strftime('%H:%M:%S')
        print(f"[{i}/{total_charts}] {current_time} - Processing new chart: {chart_name} v{chart_version}")

        # Strategy 1: Download from existing GitHub Pages repo
        #if (chart_name, chart_version) in processed_charts:
        if False:  # If it is already in the index, we would be skipping this completely. We may want to revisit this in the future, but for now let's just fetch from OCI.
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
        if oci_pull_count >= MAX_OCI_PULLS:
            print(f" -> SKIPPING pull from oci: Pull limit of {MAX_OCI_PULLS} reached.")
            print(f"    (Also skips build from source)")
            # We could likely break here too, but eh. Let's print out the names of remaining ones, or use tarballs we do have.
            continue
        
        oci_pull_count += 1
        if run_command(oci_pull_command, suppress_error=True, suppress_output=True):
            print(f"   -> SUCCESS: Pulled from OCI.")
            if not os.path.exists(package_path):
                print(f"   -> WARNING: Helm pull reported success, but package not found. Building from source.")
            else:
                continue
        else:
            print(f"   - OCI pull failed. Will build from source.")

        # Strategy 3: Build from source (with a limit)
        if source_build_count >= MAX_SOURCE_BUILDS:
            print(f" -> SKIPPING build from source: Build limit of {MAX_SOURCE_BUILDS} reached.")
            continue

        source_build_count += 1
        print(f" - Building from source ({source_build_count}/{MAX_SOURCE_BUILDS}): {chart_dir}")

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

    # First, attempt to merge with the existing index.
    if os.path.exists(index_path):
        print(" - Attempting to merge with existing index.yaml...")
        merge_cmd = ["helm", "repo", "index", package_dir, "--url", repo_url, "--merge", index_path]
        if not run_command(merge_cmd):
            print(" -> WARNING: Merge failed, likely due to a malformed remote index. Retrying without merge...")
            # If merge fails, generate a new index from scratch.
            index_cmd = ["helm", "repo", "index", package_dir, "--url", repo_url]
            if not run_command(index_cmd):
                print("\nFATAL: Failed to generate index.yaml even without merging. Exiting.")
                exit(1)
    else:
        # If no index exists, create a new one.
        index_cmd = ["helm", "repo", "index", package_dir, "--url", repo_url]
        if not run_command(index_cmd):
            print("\nFATAL: Failed to generate a new index.yaml. Exiting.")
            exit(1)

    if not os.path.exists(index_path):
        print("\nFATAL: index.yaml was not created. Exiting.")
        exit(1)

    print(f"\nSuccessfully generated index.yaml.")

    post_process_index(index_path)
    validate_index(package_dir)

    print("\n--- Repository Ready ---")
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
