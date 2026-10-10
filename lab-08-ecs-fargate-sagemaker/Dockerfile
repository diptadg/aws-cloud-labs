# CITS5503 Lab 8 - Jupyter notebook image for the SageMaker tuning job.
#
# Based on the lab sheet's Dockerfile, with four changes:
#
#   * the token, port, base image and notebook URL are build arguments, so
#     lab8_image.sh passes them from lab.env instead of this file carrying a
#     second copy of each setting
#   * pip runs with --no-cache-dir. The sheet's version leaves roughly half a
#     gigabyte of wheel cache inside the image, which is dead weight in every
#     layer export and every push
#   * the four RUN steps are collapsed into two, since each RUN is a separate
#     layer to compress and upload
#   * ADD fetches the notebook instead of wget, so the build does not depend on
#     wget being present. That is what lets BASE_IMAGE=python:3.10-slim work,
#     which cuts roughly another 800 MB
#   * sagemaker is pinned below 3. An unpinned install now gets SDK v3, which
#     (a) moved image_uris to sagemaker.core.image_uris, breaking the notebook's
#     import, and (b) depends on torch and around twenty NVIDIA CUDA packages -
#     several gigabytes of GPU libraries this CPU-only container never uses

ARG BASE_IMAGE=python:3.10
FROM ${BASE_IMAGE}

ARG JUPYTER_TOKEN
ARG JUPYTER_PORT=8888
ARG NOTEBOOK_URL=https://raw.githubusercontent.com/zhangzhics/CITS5503_Sem2/master/Labs/src/LabAI.ipynb
ARG SAGEMAKER_SPEC="sagemaker>=2.200,<3"

# No default token: an empty one would leave the notebook open to anyone who
# finds the task's public IP. lab8_image.sh passes LAB8_JUPYTER_TOKEN.
RUN test -n "${JUPYTER_TOKEN}" \
 || { echo "build with --build-arg JUPYTER_TOKEN=<token>" >&2; exit 1; }

RUN pip install --no-cache-dir jupyter boto3 "${SAGEMAKER_SPEC}" awscli

# Access token, supplied at build time (and again by the ECS task definition)
ENV JUPYTER_ENABLE_LAB=yes
ENV JUPYTER_TOKEN=${JUPYTER_TOKEN}
ENV JUPYTER_PORT=${JUPYTER_PORT}

# Allow access from ALL IPs
RUN mkdir -p /notebook \
 && jupyter notebook --generate-config \
 && echo "c.NotebookApp.ip = '0.0.0.0'" >> /root/.jupyter/jupyter_notebook_config.py

# Copy the ipynb file
ADD ${NOTEBOOK_URL} /notebook/

WORKDIR /notebook
EXPOSE ${JUPYTER_PORT}

# Shell form, not exec form: exec form does not expand ${JUPYTER_PORT}, and a
# literal 8888 here would be a second copy of the port that EXPOSE, the security
# group and the ECS port mapping all have to be kept in step with by hand.
CMD jupyter notebook --ip=0.0.0.0 --port=${JUPYTER_PORT} --no-browser --allow-root
