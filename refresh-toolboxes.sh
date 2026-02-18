#!/usr/bin/env bash

set -e

# List of all known toolboxes and their configurations
declare -A TOOLBOXES

TOOLBOXES["llama-vulkan-amdvlk"]="docker.io/kyuz0/amd-r9700-toolboxes:vulkan-amdvlk --device /dev/dri --group-add video --security-opt seccomp=unconfined"
TOOLBOXES["llama-vulkan-radv"]="docker.io/kyuz0/amd-r9700-toolboxes:vulkan-radv --device /dev/dri --group-add video --security-opt seccomp=unconfined"
TOOLBOXES["llama-rocm-6.4.4"]="docker.io/kyuz0/amd-r9700-toolboxes:rocm-6.4.4 --device /dev/dri --device /dev/kfd --group-add video --group-add render --group-add sudo --security-opt seccomp=unconfined"
TOOLBOXES["llama-rocm-6.4.4-rocwmma"]="docker.io/kyuz0/amd-r9700-toolboxes:rocm-6.4.4-rocwmma --device /dev/dri --device /dev/kfd --group-add video --group-add render --group-add sudo --security-opt seccomp=unconfined"
TOOLBOXES["llama-rocm-7.1.1"]="docker.io/kyuz0/amd-r9700-toolboxes:rocm-7.1.1 --device /dev/dri --device /dev/kfd --group-add video --group-add render --group-add sudo --security-opt seccomp=unconfined"
TOOLBOXES["llama-rocm-7.1.1-mmf"]="docker.io/kyuz0/amd-r9700-toolboxes:rocm-7.1.1-mmf --device /dev/dri --device /dev/kfd --group-add video --group-add render --group-add sudo --security-opt seccomp=unconfined"
TOOLBOXES["llama-rocm-7.1.1-rocwmma"]="docker.io/kyuz0/amd-r9700-toolboxes:rocm-7.1.1-rocwmma --device /dev/dri --device /dev/kfd --group-add video --group-add render --group-add sudo --security-opt seccomp=unconfined"
TOOLBOXES["llama-rocm-7.9"]="docker.io/kyuz0/amd-r9700-toolboxes:rocm-7.9 --device /dev/dri --device /dev/kfd --group-add video --group-add render --group-add sudo --security-opt seccomp=unconfined"
TOOLBOXES["llama-rocm-7.9-rocwmma"]="docker.io/kyuz0/amd-r9700-toolboxes:rocm-7.9-rocwmma --device /dev/dri --device /dev/kfd --group-add video --group-add render --group-add sudo --security-opt seccomp=unconfined"
TOOLBOXES["llama-rocm-7-nightly"]="docker.io/kyuz0/amd-r9700-toolboxes:rocm-7-nightly --device /dev/dri --device /dev/kfd --group-add video --group-add render --group-add sudo --security-opt seccomp=unconfined"
TOOLBOXES["llama-rocm-7-nightly-rocwmma"]="docker.io/kyuz0/amd-r9700-toolboxes:rocm-7-nightly-rocwmma --device /dev/dri --device /dev/kfd --group-add video --group-add render --group-add sudo --security-opt seccomp=unconfined"

function usage() {
  echo "Usage: $0 [all|toolbox-name1 toolbox-name2 ...]"
  echo "Available toolboxes:"
  for name in "${!TOOLBOXES[@]}"; do
    echo "  - $name"  
  done
  exit 1
}

# Check dependencies
for cmd in podman toolbox; do
  command -v "$cmd" > /dev/null || { echo "Error: '$cmd' is not installed." >&2; exit 1; }
done

if [ "$#" -lt 1 ]; then
  usage
fi

# Determine which toolboxes to refresh
if [ "$1" = "all" ]; then
  SELECTED_TOOLBOXES=("${!TOOLBOXES[@]}")
else
  SELECTED_TOOLBOXES=()
  for arg in "$@"; do
    if [[ -v TOOLBOXES["$arg"] ]]; then
      SELECTED_TOOLBOXES+=("$arg")
    else
      echo "Error: Unknown toolbox '$arg'"
      usage
    fi
  done
fi

# Loop through selected toolboxes
for name in "${SELECTED_TOOLBOXES[@]}"; do
  config="${TOOLBOXES[$name]}"
  image=$(echo "$config" | awk '{print $1}')
  options="${config#* }"

  echo "🔄 Refreshing $name (image: $image)"

  # Remove the toolbox if it exists
  if toolbox list | grep -q "$name"; then
    echo "🧹 Removing existing toolbox: $name"
    toolbox rm -f "$name"
  fi

  echo "⬇️ Pulling latest image: $image"
  podman pull "$image"

  # Identify current image ID/digest for this tag
  new_id="$(podman image inspect --format '{{.Id}}' "$image" 2>/dev/null || true)"
  new_id="${new_id:0:12}"  # truncate to short ID (podman images uses 12-char)
  new_digest="$(podman image inspect --format '{{.Digest}}' "$image" 2>/dev/null || true)"

  echo "📦 Recreating toolbox: $name"
  toolbox create "$name" --image "$image" -- $options

  # Verify the container was actually created
  if ! podman container exists "$name" 2>/dev/null; then
    echo "⚠️  toolbox create did not persist container, retrying without extra options..."
    toolbox create "$name" --image "$image"
  fi

  # Final verification
  if podman container exists "$name" 2>/dev/null; then
    echo "✅ Container verified: $name"
  else
    echo "❌ FAILED to create container: $name" >&2
    continue
  fi

  # --- Cleanup: keep only the most recent image for this tag ---
  repo="${image%:*}"
  tag="${image##*:}"

  # Guard: skip cleanup if we couldn't determine the new image digest
  if [[ -z "$new_id" || -z "$new_digest" ]]; then
    echo "⚠️  Skipping image cleanup (could not determine new image ID/digest)"
  else
    # Remove any other local images still carrying this exact tag but not the newest digest
    while read -r id ref dig; do
      if [[ "$id" != "$new_id" ]]; then
        echo "  🗑️  Removing old image: $id"
        podman image rm -f "$id" >/dev/null 2>&1 || true
      fi
    done < <(podman images --digests --format '{{.ID}} {{.Repository}}:{{.Tag}} {{.Digest}}' \
             | awk -v ref="$image" -v ndig="$new_digest" '$2==ref && $3!=ndig')

    # Remove dangling images from this repository (typically prior pulls of this tag)
    while read -r id; do
      echo "  🗑️  Removing dangling image: $id"
      podman image rm -f "$id" >/dev/null 2>&1 || true
    done < <(podman images --format '{{.ID}} {{.Repository}}:{{.Tag}}' \
             | awk -v r="$repo" '$2==r":<none>" {print $1}')
  fi
  # --- end cleanup ---

  echo "✅ $name refreshed"
  echo
done
