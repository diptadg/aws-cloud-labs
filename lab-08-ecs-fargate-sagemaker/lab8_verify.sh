#!/usr/bin/env bash
# CITS5503 Lab 8 - read-only rehearsal of every stage you have to demonstrate.
#
# Creates nothing and deletes nothing. Run it right before the demo so a broken
# service or an expired public IP shows up here rather than in front of the
# facilitator.
#
#   ./lab8_verify.sh

set -uo pipefail   # deliberately not -e: every stage should report, not abort

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lab8_lib.sh
source "${HERE}/lab8_lib.sh"

lab_env_load
# Names, which ./lab8_prepare.sh writes. Missing here means Lab 8 was never set
# up, so there is nothing to verify.
lab_env_require LAB_PREFIX AWS_REGION LAB8_IMAGE_NAME LAB8_IMAGE_TAG \
                LAB8_ECR_REPO LAB8_BUCKET LAB8_S3_PREFIX LAB8_CLUSTER \
                LAB8_SERVICE_NAME LAB8_TASK_FAMILY LAB8_CONTAINER \
                LAB8_JUPYTER_PORT LAB8_JUPYTER_TOKEN LAB8_TUNING_JOB \
                LAB8_NOTEBOOK LAB8_TASK_ROLE LAB8_EXEC_ROLE

# Values a later stage discovers. Absent just means you have not got that far,
# which is a finding to report rather than a reason to refuse to run: a partial
# run is exactly what you want when you are halfway through the lab.
LAB8_IMAGE_ARCH="${LAB8_IMAGE_ARCH:-}"
LAB8_IMAGE_URI="${LAB8_IMAGE_URI:-}"
LAB8_SG_ID="${LAB8_SG_ID:-}"
LAB8_PUBLIC_IP="${LAB8_PUBLIC_IP:-}"
LAB8_ECR_URI="${LAB8_ECR_URI:-}"

pass=0; fail=0
ok()   { printf '  [ ok ] %s\n' "$*"; pass=$((pass+1)); }
no()   { printf '  [FAIL] %s\n' "$*"; fail=$((fail+1)); }
info() { printf '         %s\n' "$*"; }

hdr() {
  echo
  echo "--------------------------------------------------------------"
  echo " $*"
  echo "--------------------------------------------------------------"
}

echo "=============================================================="
echo " CITS5503 Lab 8 verification - ${LAB_PREFIX} in ${AWS_REGION}"
echo "=============================================================="

# ============================================ Stage 1: the local Docker image
hdr "Stage 1  Dockerfile and local image"

if [[ -f "${HERE}/Dockerfile" ]]; then
  ok "Dockerfile present at ${HERE}/Dockerfile"
else
  no "Dockerfile missing"
fi

IMAGE="${LAB8_IMAGE_NAME}:${LAB8_IMAGE_TAG}"
if docker image inspect "${IMAGE}" >/dev/null 2>&1; then
  arch="$(docker image inspect "${IMAGE}" --format '{{.Architecture}}')"
  bytes="$(docker image inspect "${IMAGE}" --format '{{.Size}}')"
  ok "local image ${IMAGE} exists ($(awk -v s="${bytes}" 'BEGIN{printf "%.2f", s/1073741824}') GB, ${arch})"
  info "the view to show: docker image ls | grep ${LAB8_IMAGE_NAME}"
  if [[ -z "${LAB8_IMAGE_ARCH}" ]]; then
    no "LAB8_IMAGE_ARCH not recorded yet - re-run ./lab8_image.sh"
  elif [[ "${arch}" == "${LAB8_IMAGE_ARCH}" ]]; then
    ok "architecture matches LAB8_IMAGE_ARCH"
  else
    no "image is ${arch} but lab.env says LAB8_IMAGE_ARCH=${LAB8_IMAGE_ARCH}"
    info "re-run ./lab8_image.sh so the ECS runtimePlatform matches"
  fi
else
  no "local image ${IMAGE} not found - run ./lab8_image.sh"
fi

# ============================================================ Stage 2: ECR
hdr "Stage 2  ECR repository and pushed image"

if repo_json="$(aws ecr describe-repositories \
                  --repository-names "${LAB8_ECR_REPO}" \
                  --region "${AWS_REGION}" --output json 2>/dev/null)"; then
  uri="$(python3 -c 'import json,sys;print(json.load(sys.stdin)["repositories"][0]["repositoryUri"])' <<<"${repo_json}")"
  ok "repository ${LAB8_ECR_REPO} exists"
  info "URI ${uri}"
  if [[ "${uri}" == "${LAB8_ECR_URI:-}" ]]; then
    ok "LAB8_ECR_URI in lab.env matches"
  else
    no "LAB8_ECR_URI in lab.env is '${LAB8_ECR_URI:-<unset>}'"
    info "re-run python3 lab8_ecr.py"
  fi

  if img="$(aws ecr describe-images \
              --repository-name "${LAB8_ECR_REPO}" \
              --image-ids "imageTag=${LAB8_IMAGE_TAG}" \
              --region "${AWS_REGION}" \
              --query 'imageDetails[0].[imagePushedAt,imageSizeInBytes]' \
              --output text 2>/dev/null)"; then
    pushed="$(awk '{print $1}' <<<"${img}")"
    size="$(awk '{print $2}' <<<"${img}")"
    ok "image tagged '${LAB8_IMAGE_TAG}' pushed at ${pushed} ($(awk -v s="${size}" 'BEGIN{printf "%.2f", s/1073741824}') GB compressed)"
  else
    no "no image tagged '${LAB8_IMAGE_TAG}' in ${LAB8_ECR_REPO} - run ./lab8_push.sh"
  fi
else
  no "repository ${LAB8_ECR_REPO} not found - run python3 lab8_ecr.py"
fi

# ======================================================= Stage 3: ECS deploy
hdr "Stage 3  ECS cluster, service and running task"

cl_status="$(aws ecs describe-clusters --clusters "${LAB8_CLUSTER}" \
               --region "${AWS_REGION}" \
               --query 'clusters[0].status' --output text 2>/dev/null)"
if [[ "${cl_status}" == "ACTIVE" ]]; then
  ok "cluster ${LAB8_CLUSTER} is ACTIVE"
else
  no "cluster ${LAB8_CLUSTER} status is '${cl_status:-missing}'"
fi

svc="$(aws ecs describe-services --cluster "${LAB8_CLUSTER}" \
         --services "${LAB8_SERVICE_NAME}" --region "${AWS_REGION}" \
         --query 'services[0].[status,desiredCount,runningCount,launchType,taskDefinition]' \
         --output text 2>/dev/null)"
if [[ -n "${svc}" && "${svc}" != "None"* ]]; then
  read -r s_status s_desired s_running s_launch s_td <<<"${svc}"
  if [[ "${s_status}" == "ACTIVE" ]]; then
    ok "service ${LAB8_SERVICE_NAME} is ACTIVE (${s_launch})"
  else
    no "service ${LAB8_SERVICE_NAME} status is ${s_status}"
  fi
  if [[ "${s_running}" == "${s_desired}" ]]; then
    ok "running ${s_running} of ${s_desired} desired task(s)"
  else
    no "running ${s_running} of ${s_desired} desired task(s)"
    info "aws ecs describe-services --cluster ${LAB8_CLUSTER} --services ${LAB8_SERVICE_NAME} --region ${AWS_REGION} --query 'services[0].events[:5]'"
  fi
  info "task definition ${s_td##*/}"
else
  no "service ${LAB8_SERVICE_NAME} not found - run python3 lab8_ecs.py"
fi

td="$(aws ecs describe-task-definition --task-definition "${LAB8_TASK_FAMILY}" \
        --region "${AWS_REGION}" \
        --query 'taskDefinition.[networkMode,cpu,memory,requiresCompatibilities[0],taskRoleArn,executionRoleArn]' \
        --output text 2>/dev/null)"
if [[ -n "${td}" ]]; then
  read -r t_net t_cpu t_mem t_compat t_task_role t_exec_role <<<"${td}"
  [[ "${t_net}" == "awsvpc" ]] && ok "networkMode awsvpc" || no "networkMode is ${t_net}"
  [[ "${t_compat}" == "FARGATE" ]] && ok "requiresCompatibilities FARGATE" || no "compatibility is ${t_compat}"
  ok "cpu ${t_cpu}, memory ${t_mem}"
  [[ "${t_task_role##*/}" == "${LAB8_TASK_ROLE}" ]] \
    && ok "task role ${LAB8_TASK_ROLE}" \
    || no "task role is ${t_task_role##*/}, lab.env says ${LAB8_TASK_ROLE}"
  [[ "${t_exec_role##*/}" == "${LAB8_EXEC_ROLE}" ]] \
    && ok "execution role ${LAB8_EXEC_ROLE}" \
    || no "execution role is ${t_exec_role##*/}, lab.env says ${LAB8_EXEC_ROLE}"
  port="$(aws ecs describe-task-definition --task-definition "${LAB8_TASK_FAMILY}" \
            --region "${AWS_REGION}" \
            --query "taskDefinition.containerDefinitions[?name=='${LAB8_CONTAINER}'].portMappings[0].containerPort | [0]" \
            --output text 2>/dev/null)"
  if [[ "${port}" == "${LAB8_JUPYTER_PORT}" ]]; then
    ok "container ${LAB8_CONTAINER} maps port ${port}"
  else
    no "container port is '${port}', lab.env says ${LAB8_JUPYTER_PORT}"
  fi
else
  no "no task definition in family ${LAB8_TASK_FAMILY}"
fi

# The task's own image URI and launch type are what gets shown in the console.
run_task="$(aws ecs list-tasks --cluster "${LAB8_CLUSTER}" \
              --service-name "${LAB8_SERVICE_NAME}" --desired-status RUNNING \
              --region "${AWS_REGION}" --query 'taskArns[0]' --output text 2>/dev/null)"
if [[ -n "${run_task}" && "${run_task}" != "None" ]]; then
  t_info="$(aws ecs describe-tasks --cluster "${LAB8_CLUSTER}" --tasks "${run_task}" \
              --region "${AWS_REGION}" \
              --query 'tasks[0].[launchType,lastStatus,containers[0].image]' \
              --output text 2>/dev/null)"
  read -r r_launch r_status r_image <<<"${t_info}"
  [[ "${r_launch}" == "FARGATE" ]] \
    && ok "the running task uses FARGATE" \
    || no "the running task launch type is ${r_launch}"
  [[ "${r_status}" == "RUNNING" ]] \
    && ok "task lastStatus RUNNING" \
    || no "task lastStatus is ${r_status}"
  if [[ -z "${LAB8_IMAGE_URI}" ]]; then
    no "LAB8_IMAGE_URI not recorded yet - run ./lab8_push.sh"
    info "the task is running ${r_image}"
  elif [[ "${r_image}" == "${LAB8_IMAGE_URI}" ]]; then
    ok "task image URI matches the pushed ECR image"
    info "${r_image}"
  else
    no "task image is ${r_image}"
    info "lab.env LAB8_IMAGE_URI is ${LAB8_IMAGE_URI}"
    info "the console has to show the same repository name and tag as ECR"
  fi
else
  no "no RUNNING task to inspect in ${LAB8_CLUSTER}"
fi

# security group: inbound Jupyter port
if [[ -n "${LAB8_SG_ID:-}" ]]; then
  ing="$(aws ec2 describe-security-groups --group-ids "${LAB8_SG_ID}" \
           --region "${AWS_REGION}" \
           --query "SecurityGroups[0].IpPermissions[?FromPort==\`${LAB8_JUPYTER_PORT}\`].IpRanges[0].CidrIp | [0]" \
           --output text 2>/dev/null)"
  if [[ -n "${ing}" && "${ing}" != "None" ]]; then
    ok "security group ${LAB8_SG_ID} allows inbound ${LAB8_JUPYTER_PORT} from ${ing}"
  else
    no "security group ${LAB8_SG_ID} has no inbound rule for ${LAB8_JUPYTER_PORT}"
  fi
else
  no "LAB8_SG_ID not in lab.env - run python3 lab8_ecs.py"
fi

# ============================================== Stage 4: Jupyter reachability
hdr "Stage 4  Jupyter reachable on the task's public IP"

task_arn="$(aws ecs list-tasks --cluster "${LAB8_CLUSTER}" \
              --service-name "${LAB8_SERVICE_NAME}" --desired-status RUNNING \
              --region "${AWS_REGION}" --query 'taskArns[0]' --output text 2>/dev/null)"

if [[ -n "${task_arn}" && "${task_arn}" != "None" ]]; then
  ok "running task ${task_arn##*/}"
  eni="$(aws ecs describe-tasks --cluster "${LAB8_CLUSTER}" --tasks "${task_arn}" \
           --region "${AWS_REGION}" \
           --query 'tasks[0].attachments[0].details[?name==`networkInterfaceId`].value' \
           --output text 2>/dev/null)"
  ip="$(aws ec2 describe-network-interfaces --network-interface-ids "${eni}" \
          --region "${AWS_REGION}" \
          --query 'NetworkInterfaces[0].Association.PublicIp' --output text 2>/dev/null)"

  if [[ -n "${ip}" && "${ip}" != "None" ]]; then
    ok "public IP ${ip}"
    if [[ "${ip}" != "${LAB8_PUBLIC_IP:-}" ]]; then
      no "LAB8_PUBLIC_IP in lab.env is '${LAB8_PUBLIC_IP:-<unset>}' - stale"
      info "refresh it with ./lab8_ip.sh"
    else
      ok "LAB8_PUBLIC_IP matches"
    fi

    url="http://${ip}:${LAB8_JUPYTER_PORT}"
    code="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 10 \
              "${url}/api?token=${LAB8_JUPYTER_TOKEN}" 2>/dev/null)"
    if [[ "${code}" == "200" ]]; then
      ok "Jupyter answers on ${url} with the lab token"
      if curl -sS --max-time 10 "${url}/api/contents?token=${LAB8_JUPYTER_TOKEN}" 2>/dev/null \
           | grep -q "${LAB8_NOTEBOOK}"; then
        ok "${LAB8_NOTEBOOK} is present in the container"
      else
        no "${LAB8_NOTEBOOK} not listed by the running server"
      fi
      info "open ${url}/?token=${LAB8_JUPYTER_TOKEN}"
    else
      no "Jupyter did not answer (HTTP '${code:-none}')"
      info "the UWA network blocks some outbound ports; try a phone hotspot"
      info "the container also takes a minute to start after the task goes RUNNING"
    fi
  else
    no "the task has no public IPv4 address"
  fi
else
  no "no RUNNING task in ${LAB8_CLUSTER}/${LAB8_SERVICE_NAME}"
fi

# ============================================ Stage 5: SageMaker tuning job
hdr "Stage 5  SageMaker hyperparameter tuning job and S3 output"

if aws s3api head-bucket --bucket "${LAB8_BUCKET}" --region "${AWS_REGION}" >/dev/null 2>&1; then
  ok "bucket ${LAB8_BUCKET} exists"
  n_train="$(aws s3 ls "s3://${LAB8_BUCKET}/${LAB8_S3_PREFIX}/train/" --region "${AWS_REGION}" 2>/dev/null | wc -l)"
  n_valid="$(aws s3 ls "s3://${LAB8_BUCKET}/${LAB8_S3_PREFIX}/validation/" --region "${AWS_REGION}" 2>/dev/null | wc -l)"
  (( n_train > 0 )) && ok "train/ has ${n_train} object(s)" || no "train/ is empty - the notebook has not uploaded train.csv"
  (( n_valid > 0 )) && ok "validation/ has ${n_valid} object(s)" || no "validation/ is empty"

  # The checkpoint asks for two folders under output/, each named with your
  # prefix and each holding a model.tar.gz.
  mapfile -t out_dirs < <(aws s3 ls "s3://${LAB8_BUCKET}/${LAB8_S3_PREFIX}/output/" \
                            --region "${AWS_REGION}" 2>/dev/null \
                          | awk '$1=="PRE"{sub(/\/$/,"",$2); print $2}')
  if (( ${#out_dirs[@]} == 2 )); then
    ok "output/ has exactly 2 folders"
  else
    no "output/ has ${#out_dirs[@]} folder(s), the checkpoint expects 2"
  fi
  for d in "${out_dirs[@]}"; do
    if [[ "${d}" == "${LAB_PREFIX}"* ]]; then
      ok "output/${d} is named with your prefix"
    else
      no "output/${d} does not start with ${LAB_PREFIX}"
    fi
    if aws s3 ls "s3://${LAB8_BUCKET}/${LAB8_S3_PREFIX}/output/${d}/" --recursive \
         --region "${AWS_REGION}" 2>/dev/null | grep -q 'model\.tar\.gz'; then
      ok "output/${d} contains model.tar.gz"
    else
      no "output/${d} has no model.tar.gz"
    fi
  done
else
  no "bucket ${LAB8_BUCKET} not found - run python3 lab8_bucket.py"
fi

job="$(aws sagemaker describe-hyper-parameter-tuning-job \
         --hyper-parameter-tuning-job-name "${LAB8_TUNING_JOB}" \
         --region "${AWS_REGION}" \
         --query '[HyperParameterTuningJobStatus,TrainingJobStatusCounters.Completed,TrainingJobStatusCounters.InProgress,TrainingJobStatusCounters.Failed]' \
         --output text 2>/dev/null)"
if [[ -n "${job}" ]]; then
  read -r j_status j_done j_prog j_failed <<<"${job}"
  if [[ "${j_status}" == "Completed" ]]; then
    ok "tuning job ${LAB8_TUNING_JOB} is Completed"
  else
    no "tuning job ${LAB8_TUNING_JOB} is ${j_status} (done ${j_done}, running ${j_prog}, failed ${j_failed})"
  fi

  # The checkpoint asks for two training jobs, both Completed, both named with
  # your prefix. SageMaker derives those names from the tuning job name,
  # so a wrong LAB8_TUNING_JOB shows up here.
  mapfile -t tj < <(aws sagemaker list-training-jobs-for-hyper-parameter-tuning-job \
                      --hyper-parameter-tuning-job-name "${LAB8_TUNING_JOB}" \
                      --region "${AWS_REGION}" \
                      --query 'TrainingJobSummaries[].[TrainingJobName,TrainingJobStatus]' \
                      --output text 2>/dev/null)
  if (( ${#tj[@]} == 2 )); then
    ok "the tuning job has exactly 2 training jobs"
  else
    no "the tuning job has ${#tj[@]} training job(s), the checkpoint expects 2"
  fi
  for row in "${tj[@]}"; do
    read -r tj_name tj_status <<<"${row}"
    [[ "${tj_name}" == "${LAB_PREFIX}"* ]] \
      && ok "${tj_name} is named with your prefix" \
      || no "${tj_name} does not start with ${LAB_PREFIX}"
    [[ "${tj_status}" == "Completed" ]] \
      && ok "${tj_name} is Completed" \
      || no "${tj_name} is ${tj_status}"
  done

  best="$(aws sagemaker describe-hyper-parameter-tuning-job \
            --hyper-parameter-tuning-job-name "${LAB8_TUNING_JOB}" \
            --region "${AWS_REGION}" \
            --query 'BestTrainingJob.FinalHyperParameterTuningJobObjectiveMetric.[MetricName,Value]' \
            --output text 2>/dev/null)"
  [[ -n "${best}" && "${best}" != "None"* ]] && info "best ${best}"
else
  no "tuning job ${LAB8_TUNING_JOB} not found - run the notebook's launch cell"
  info "the name has to be unique; a rerun needs a different LAB8_TUNING_JOB"
fi

# ================================================== Stage 6: cleanup readiness
hdr "Stage 6  Cleanup"

echo "  Nothing is deleted by this script. When the demo is done:"
echo "    python3 lab8_cleanup.py --all"
echo "  which follows the order the checkpoint asks for in the console:"
echo "    1. ECR repository and its images"
echo "    2. ECS service, task definitions, cluster, then the security group"
echo "    3. S3 bucket, emptied then deleted"
echo "  and finally the CloudWatch log group."

echo
echo "=============================================================="
printf ' %d passed, %d failed\n' "${pass}" "${fail}"
echo "=============================================================="
(( fail == 0 )) || exit 1
