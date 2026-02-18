#!/usr/bin/env python3
"""Refresh llama.cpp toolbox containers with latest images.

Usage:
    refresh-toolboxes.py [all | toolbox-name ...]

Pulls the latest image for each selected toolbox, recreates the container,
and cleans up old images.
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass


@dataclass(frozen=True)
class Toolbox:
    image: str
    options: list[str]


# Registry of all known toolboxes
VULKAN_OPTS = ["--device", "/dev/dri", "--group-add", "video",
               "--security-opt", "seccomp=unconfined"]
ROCM_OPTS = ["--device", "/dev/dri", "--device", "/dev/kfd",
             "--group-add", "video", "--group-add", "render",
             "--group-add", "sudo", "--security-opt", "seccomp=unconfined"]

REGISTRY = "docker.io/kyuz0/amd-r9700-toolboxes"

TOOLBOXES: dict[str, Toolbox] = {
    "llama-vulkan-amdvlk":          Toolbox(f"{REGISTRY}:vulkan-amdvlk", VULKAN_OPTS),
    "llama-vulkan-radv":            Toolbox(f"{REGISTRY}:vulkan-radv", VULKAN_OPTS),
    "llama-rocm-6.4.4":            Toolbox(f"{REGISTRY}:rocm-6.4.4", ROCM_OPTS),
    "llama-rocm-6.4.4-rocwmma":    Toolbox(f"{REGISTRY}:rocm-6.4.4-rocwmma", ROCM_OPTS),
    "llama-rocm-7.1.1":            Toolbox(f"{REGISTRY}:rocm-7.1.1", ROCM_OPTS),
    "llama-rocm-7.1.1-mmf":        Toolbox(f"{REGISTRY}:rocm-7.1.1-mmf", ROCM_OPTS),
    "llama-rocm-7.1.1-rocwmma":    Toolbox(f"{REGISTRY}:rocm-7.1.1-rocwmma", ROCM_OPTS),
    "llama-rocm-7.9":              Toolbox(f"{REGISTRY}:rocm-7.9", ROCM_OPTS),
    "llama-rocm-7.9-rocwmma":      Toolbox(f"{REGISTRY}:rocm-7.9-rocwmma", ROCM_OPTS),
    "llama-rocm-7-nightly":        Toolbox(f"{REGISTRY}:rocm-7-nightly", ROCM_OPTS),
    "llama-rocm-7-nightly-rocwmma": Toolbox(f"{REGISTRY}:rocm-7-nightly-rocwmma", ROCM_OPTS),
}


def run(cmd: list[str], *, check: bool = True, capture: bool = False) -> subprocess.CompletedProcess:
    """Run a command, printing it for visibility."""
    return subprocess.run(cmd, check=check, capture_output=capture, text=True)


def container_exists(name: str) -> bool:
    return run(["podman", "container", "exists", name], check=False).returncode == 0


def image_short_id(image: str) -> str | None:
    """Get the short (12-char) image ID, or None if not found."""
    result = run(["podman", "image", "inspect", "--format", "{{.Id}}", image],
                 check=False, capture=True)
    if result.returncode != 0 or not result.stdout.strip():
        return None
    return result.stdout.strip()[:12]


def image_digest(image: str) -> str | None:
    """Get the image digest, or None if not found."""
    result = run(["podman", "image", "inspect", "--format", "{{.Digest}}", image],
                 check=False, capture=True)
    if result.returncode != 0 or not result.stdout.strip():
        return None
    return result.stdout.strip()


def cleanup_old_images(image: str, keep_id: str, keep_digest: str) -> None:
    """Remove old images for the same tag, keeping only the current one."""
    repo_tag = image  # e.g. docker.io/kyuz0/...:rocm-7.1.1-rocwmma
    repo = image.rsplit(":", 1)[0]

    # Get all images with digests
    result = run(["podman", "images", "--digests",
                  "--format", "{{.ID}} {{.Repository}}:{{.Tag}} {{.Digest}}"],
                 capture=True)

    for line in result.stdout.strip().splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue
        img_id, img_ref, img_dig = parts[0], parts[1], parts[2]

        # Remove old tagged images (same tag, different digest)
        if img_ref == repo_tag and img_dig != keep_digest and img_id != keep_id:
            print(f"  🗑️  Removing old image: {img_id}")
            run(["podman", "image", "rm", "-f", img_id], check=False)

        # Remove dangling images from same repo
        if img_ref == f"{repo}:<none>":
            print(f"  🗑️  Removing dangling image: {img_id}")
            run(["podman", "image", "rm", "-f", img_id], check=False)


def refresh(name: str, toolbox: Toolbox) -> bool:
    """Refresh a single toolbox. Returns True on success."""
    print(f"🔄 Refreshing {name} (image: {toolbox.image})")

    # Remove existing container
    if container_exists(name):
        print(f"🧹 Removing existing toolbox: {name}")
        run(["toolbox", "rm", "-f", name])

    # Pull latest image
    print(f"⬇️  Pulling latest image: {toolbox.image}")
    run(["podman", "pull", toolbox.image])

    # Get new image identifiers
    new_id = image_short_id(toolbox.image)
    new_digest = image_digest(toolbox.image)

    # Create toolbox
    print(f"📦 Recreating toolbox: {name}")
    create_cmd = ["toolbox", "create", name, "--image", toolbox.image, "--"] + toolbox.options
    result = run(create_cmd, check=False)

    # Retry without extra options if first attempt failed
    if not container_exists(name):
        print("⚠️  toolbox create did not persist container, retrying without extra options...")
        run(["toolbox", "create", name, "--image", toolbox.image], check=False)

    # Final verification
    if not container_exists(name):
        print(f"❌ FAILED to create container: {name}", file=sys.stderr)
        return False

    print(f"✅ Container verified: {name}")

    # Cleanup old images
    if new_id and new_digest:
        cleanup_old_images(toolbox.image, new_id, new_digest)
    else:
        print("⚠️  Skipping image cleanup (could not determine new image ID/digest)")

    print(f"✅ {name} refreshed\n")
    return True


def main() -> None:
    # Check dependencies
    for cmd in ("podman", "toolbox"):
        if run(["command", "-v", cmd], check=False, capture=True).returncode != 0:
            # Fallback: try which
            if run(["which", cmd], check=False, capture=True).returncode != 0:
                print(f"Error: '{cmd}' is not installed.", file=sys.stderr)
                sys.exit(1)

    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} [all | toolbox-name ...]")
        print("Available toolboxes:")
        for name in sorted(TOOLBOXES):
            print(f"  - {name}")
        sys.exit(1)

    # Determine which toolboxes to refresh
    if sys.argv[1] == "all":
        selected = list(TOOLBOXES.keys())
    else:
        selected = []
        for arg in sys.argv[1:]:
            if arg not in TOOLBOXES:
                print(f"Error: Unknown toolbox '{arg}'")
                print(f"Available: {', '.join(sorted(TOOLBOXES))}")
                sys.exit(1)
            selected.append(arg)

    # Refresh each selected toolbox
    failures = []
    for name in selected:
        if not refresh(name, TOOLBOXES[name]):
            failures.append(name)

    if failures:
        print(f"\n❌ Failed: {', '.join(failures)}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
