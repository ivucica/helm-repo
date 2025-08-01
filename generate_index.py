#!/usr/bin/env python3
# run inside of the cloned repo.

import os
import subprocess
from typing import List

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

def run_command(command: list) -> None:
    """Runs a shell command and checks for errors."""
    try:
        process = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=True
        )
        if process.stdout:
            # Suppress noisy output from helm dependency build for cleaner logs
            if "dependency build" not in " ".join(command):
                 print(process.stdout)
    except FileNotFoundError:
        print(f"Error: The command '{command[0]}' was not found.")
        print("Please ensure the Helm CLI is installed and in your system's PATH.")
        exit(1)
    except subprocess.CalledProcessError as e:
        print(f"Error executing command: {' '.join(command)}")
        print(f"Stderr: {e.stderr}")
        raise

def create_helm_index(repo_path: str, repo_url: str):
    """
    Creates a Helm repository index.yaml file by finding, packaging,
    and indexing charts.
    """
    charts_root = os.path.join(repo_path, "charts")
    package_dir = os.path.join(repo_path, "helm-repo")
    os.makedirs(package_dir, exist_ok=True)
    print(f"Chart packages will be placed in: {package_dir}\\n")

    chart_subdirs = ["stable", "premium", "incubator", "system", "library"]
    all_chart_dirs = []

    print("--- 1. Scanning for charts ---")
    for subdir in chart_subdirs:
        path = os.path.join(charts_root, subdir)
        found = find_chart_directories(path)
        print(f"- Found {len(found)} charts in '{subdir}'")
        all_chart_dirs.extend(found)

    if not all_chart_dirs:
        print("\\nNo charts found. Exiting.")
        return

    print(f"\\n--- 2. Packaging {len(all_chart_dirs)} total charts ---")
    for chart_dir in all_chart_dirs:
        chart_name = os.path.basename(chart_dir)
        print(f" - Processing: {chart_name}")
        try:
            # ==================================================================
            # ===                MODIFICATION IS HERE                        ===
            # ==================================================================
            # Step 1: Build dependencies before packaging. This command will
            # download required charts (like 'common') into the chart's
            # 'charts' subdirectory.
            print(f"   - Building dependencies for {chart_name}...")
            run_command(["helm", "dependency", "build", chart_dir])

            # Step 2: Package the chart with its dependencies.
            print(f"   - Packaging {chart_name}...")
            run_command(["helm", "package", chart_dir, "--destination", package_dir])

        except subprocess.CalledProcessError:
            print(f" -> FAILED to process chart: {chart_name}")
            continue

    print(f"\\n--- 3. Generating index.yaml ---")
    print(f"Indexing packages in '{package_dir}' with URL '{repo_url}'")
    try:
        run_command(["helm", "index", package_dir, "--url", repo_url])
        index_file = os.path.join(package_dir, "index.yaml")
        print(f"\\nSuccessfully generated index.yaml at: {index_file}")
    except subprocess.CalledProcessError:
        print("Failed to generate index.yaml. Please check the errors above.")
        return

    print("\\n--- Repository Ready ---")
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




if __name__ == "__main__":
    # --- Configuration ---
    # The absolute URL where your packaged charts will be hosted.
    # IMPORTANT: You MUST change this to your own URL.
    #REPO_HOST_URL = "https://your-server.com/path-to-charts"
    REPO_HOST_URL = os.getenv("REPO_HOST_URL", "https://your-server.com/path-to-charts")

    # The local path to the cloned repository.
    # This script assumes it is located in the root of the repository.
    #REPO_LOCAL_PATH = "."
    REPO_LOCAL_PATH = os.getenv("REPO_LOCAL_PATH", ".")
    
    create_helm_index(REPO_LOCAL_PATH, REPO_HOST_URL)
