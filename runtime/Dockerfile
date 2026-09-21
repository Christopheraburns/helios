# helios custom runtime for Cloudera AI Workbench
# Base: PBJ Workbench, Python 3.12 (editor + kernel are inherited and must not change)
FROM docker.repository.cloudera.com/cloudera/cdsw/ml-runtime-pbj-workbench-python3.12-standard:2026.08.1-b5

ARG HELIOS_VERSION=0.1.0
ARG OSSIE_REF=main

USER root

# System libraries: SASL/GSSAPI headers so impyla/pyhive can be switched to Kerberos
# later without a rebuild; git for cloning the Ossie spec at build time.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      libsasl2-dev libsasl2-modules libsasl2-modules-gssapi-mit libkrb5-dev git \
 && rm -rf /var/lib/apt/lists/*

# Python dependencies (pinned in requirements.txt)
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -r /tmp/requirements.txt \
 && rm /tmp/requirements.txt

# Ossie spec + schema + example models, pinned to a ref, for validation and evaluation
RUN git clone --depth 1 --branch ${OSSIE_REF} https://github.com/apache/ossie.git /opt/ossie \
 && rm -rf /opt/ossie/.git
ENV OSSIE_HOME=/opt/ossie

# Back to the unprivileged Workbench user
USER cdsw

# ---- Cloudera ML Runtime metadata (required for registration) ----
ENV ML_RUNTIME_EDITION="helios" \
    ML_RUNTIME_SHORT_VERSION="0.1" \
    ML_RUNTIME_MAINTENANCE_VERSION="1" \
    ML_RUNTIME_FULL_VERSION="0.1.1" \
    ML_RUNTIME_DESCRIPTION="helios: semantic layer engine (Impala/Hive clients, SQLGlot, Ossie, MCP, FastAPI)"

LABEL com.cloudera.ml.runtime.edition=$ML_RUNTIME_EDITION \
      com.cloudera.ml.runtime.short-version=$ML_RUNTIME_SHORT_VERSION \
      com.cloudera.ml.runtime.maintenance-version=$ML_RUNTIME_MAINTENANCE_VERSION \
      com.cloudera.ml.runtime.full-version=$ML_RUNTIME_FULL_VERSION \
      com.cloudera.ml.runtime.description=$ML_RUNTIME_DESCRIPTION
