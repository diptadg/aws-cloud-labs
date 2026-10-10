#!/usr/bin/env bash
# CITS5503 Lab 8 - diagnose a Docker build that looks stuck.
#
# "exporting layers" is the last BuildKit step: every RUN has already finished
# and it is writing the image into the store. It has three failure modes on
# WSL2, and all three look identical from the terminal - no error, no progress.
#
#   1. the Docker VM's virtual disk is full
#   2. the Windows drive backing that virtual disk is full
#   3. Defender is scanning every block as it is written
#
# Run this from a second WSL terminal while the build sits there.
#
#   ./lab8_docker_doctor.sh          one snapshot
#   ./lab8_docker_doctor.sh --watch  re-check every 15s, to see if it is moving

set -uo pipefail

watch=0
[[ "${1:-}" == "--watch" ]] && watch=1

hdr() { echo; echo "--- $* ---"; }

snapshot() {
  hdr "Docker daemon"
  if ! docker info >/dev/null 2>&1; then
    echo "  daemon unreachable - Docker Desktop is not running, or WSL integration is off"
    return
  fi
  docker info --format '  server {{.ServerVersion}}, driver {{.Driver}}, root {{.DockerRootDir}}' 2>/dev/null

  hdr "Space inside the Docker VM"
  # This is the number that matters. The export step needs room for the whole
  # image at once, on top of the build cache that produced it.
  if ! docker run --rm -v /:/host alpine df -h /host 2>/dev/null | tail -2; then
    echo "  could not start a probe container - if THIS hangs too, the VM disk is full"
  fi

  hdr "What Docker is holding"
  docker system df 2>/dev/null

  hdr "Space on the WSL filesystem"
  df -h / | tail -1

  hdr "Running containers"
  docker ps --format '  {{.Names}}  {{.Status}}  {{.Image}}' 2>/dev/null || true
}

snapshot

if (( watch )); then
  echo
  echo "Watching. The build is progressing if 'Images space usage' or the VM's"
  echo "used figure grows between snapshots. Ctrl-C to stop."
  while true; do
    sleep 15
    echo
    echo "=============== $(date +%H:%M:%S) ==============="
    docker system df 2>/dev/null | sed -n '2,4p'
    docker run --rm -v /:/host alpine df -h /host 2>/dev/null | tail -1
  done
fi

cat <<'ADVICE'

--------------------------------------------------------------
If the numbers above are not moving, in order of likelihood:

1. The Docker VM is out of disk. Reclaim it, then rebuild:

     docker builder prune -af
     docker system prune -a --volumes

   The build cache survives a cancelled build, so a rebuild after
   freeing space skips straight back to the export step rather
   than reinstalling everything.

2. The Windows drive is full. The VM's disk is a .vhdx that grows
   on demand; when C: runs out, writes stall rather than fail.
   From PowerShell:

     Get-PSDrive C
     Get-ChildItem $env:LOCALAPPDATA\Docker\wsl -Recurse -Filter *.vhdx |
       Select-Object FullName, @{n='GB';e={[math]::Round($_.Length/1GB,1)}}

   A .vhdx does not shrink when you prune. To actually give the
   space back to Windows, after pruning:

     wsl --shutdown
     Optimize-VHD -Path <the vhdx path> -Mode Full     # needs Hyper-V

   Or, without Hyper-V, Docker Desktop -> Settings ->
   Troubleshoot -> Clean / Purge data.

3. Defender is scanning the vhdx. This can turn a two-minute
   export into twenty. From an admin PowerShell:

     Add-MpPreference -ExclusionPath "$env:LOCALAPPDATA\Docker"
     Add-MpPreference -ExclusionProcess "com.docker.backend.exe"
     Add-MpPreference -ExclusionProcess "vmmem.exe"
     Add-MpPreference -ExclusionProcess "wslservice.exe"

4. Build something smaller. The lab's base image is python:3.10,
   about 1 GB before anything is installed:

     ./lab8_image.sh --slim

   which builds on python:3.10-slim instead. Everything the lab
   needs still installs; the result is roughly a quarter the size,
   which also makes the ECR push considerably faster.
--------------------------------------------------------------
ADVICE
