# helios-ds-runtime: generator API, workers and Jobs (Cloudera AI Workbench)
# Base: PBJ Workbench, Python 3.12, same as the helios runtime.
FROM docker.repository.cloudera.com/cloudera/cdsw/ml-runtime-pbj-workbench-python3.12-standard:2026.08.1-b5

USER root

# SASL/Kerberos headers (kept in step with the helios runtime); pinned fonts for rendering.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      libsasl2-dev libsasl2-modules libsasl2-modules-gssapi-mit libkrb5-dev \
      fonts-dejavu-core \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -r /tmp/requirements.txt \
 && rm /tmp/requirements.txt

# DuckDB's tpcds extension, preinstalled so local TPC-DS generation works offline.
ENV DUCKDB_EXTENSION_DIRECTORY=/opt/duckdb/extensions
RUN mkdir -p ${DUCKDB_EXTENSION_DIRECTORY} \
 && python -c "import duckdb, os; con = duckdb.connect(); con.sql(f\"SET extension_directory='{os.environ['DUCKDB_EXTENSION_DIRECTORY']}'\"); con.sql('INSTALL tpcds')" \
 && chmod -R a+rX /opt/duckdb

USER cdsw

ENV ML_RUNTIME_EDITION="helios-ds" \
    ML_RUNTIME_SHORT_VERSION="0.1" \
    ML_RUNTIME_MAINTENANCE_VERSION="0" \
    ML_RUNTIME_FULL_VERSION="0.1.0" \
    ML_RUNTIME_DESCRIPTION="helios-ds: deterministic synthetic asset generator (PyIceberg, DuckDB, FastAPI, MCP)"

LABEL com.cloudera.ml.runtime.edition=$ML_RUNTIME_EDITION \
      com.cloudera.ml.runtime.short-version=$ML_RUNTIME_SHORT_VERSION \
      com.cloudera.ml.runtime.maintenance-version=$ML_RUNTIME_MAINTENANCE_VERSION \
      com.cloudera.ml.runtime.full-version=$ML_RUNTIME_FULL_VERSION \
      com.cloudera.ml.runtime.description=$ML_RUNTIME_DESCRIPTION
