#!/usr/bin/env bash
# CITS5503 Lab 8 - the lab sheet's AWS CLI chain for the task's public IP.
#
# Same result as `python3 lab8_ecs.py --ip-only`, kept as a separate script
# because the lab sheet asks for the CLI form. Cluster and service names come
# from lab.env rather than being pasted in.
#
#   ./lab8_ip.sh          print the IP and the notebook URL
#   ./lab8_ip.sh --url    print only the URL

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lab8_lib.sh
source "${HERE}/lab8_lib.sh"

lab_env_load
lab_env_require AWS_REGION LAB8_CLUSTER LAB8_SERVICE_NAME \
                LAB8_JUPYTER_PORT LAB8_JUPYTER_TOKEN

task_arn="$(aws ecs list-tasks \
              --cluster "${LAB8_CLUSTER}" \
              --service-name "${LAB8_SERVICE_NAME}" \
              --desired-status RUNNING \
              --region "${AWS_REGION}" \
              --query 'taskArns[0]' --output text)"

if [[ -z "${task_arn}" || "${task_arn}" == "None" ]]; then
  echo "No RUNNING task in ${LAB8_CLUSTER}/${LAB8_SERVICE_NAME}." >&2
  echo "Deploy it first:  python3 lab8_ecs.py" >&2
  exit 1
fi

eni="$(aws ecs describe-tasks \
         --cluster "${LAB8_CLUSTER}" \
         --tasks "${task_arn}" \
         --region "${AWS_REGION}" \
         --query 'tasks[0].attachments[0].details[?name==`networkInterfaceId`].value' \
         --output text)"

ip="$(aws ec2 describe-network-interfaces \
        --network-interface-ids "${eni}" \
        --region "${AWS_REGION}" \
        --query 'NetworkInterfaces[0].Association.PublicIp' \
        --output text)"

if [[ -z "${ip}" || "${ip}" == "None" ]]; then
  echo "The task has no public IPv4 address." >&2
  echo "Check that the service was created with assignPublicIp=ENABLED." >&2
  exit 1
fi

url="http://${ip}:${LAB8_JUPYTER_PORT}/?token=${LAB8_JUPYTER_TOKEN}"
lab_env_set LAB8_PUBLIC_IP "${ip}" LAB8_JUPYTER_URL "${url}"

if [[ "${1:-}" == "--url" ]]; then
  echo "${url}"
  exit 0
fi

echo "Task:       ${task_arn##*/}"
echo "Interface:  ${eni}"
echo "Public IP:  ${ip}"
echo
echo "Notebook:   ${url}"
