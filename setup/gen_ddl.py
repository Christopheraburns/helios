"""
gen_ddl.py
Emits the Impala DDL that registers the TPC-DS Parquet files produced by start.py.

  stage.sql    CREATE EXTERNAL TABLE ... LIKE PARQUET for each table  (database tpcds_stage)
  iceberg.sql  CREATE TABLE ... STORED AS ICEBERG AS SELECT ...       (database tpcds)

Run in a Workbench session after start.py has generated /home/cdsw/tpcds/<table>/data.parquet
and the files have been copied to BUCKET_PATH. Paste the resulting .sql files into Hue
(select all, then Execute) or run them with impala-shell -f.
"""
import os

BUCKET_PATH = "s3a://<bucket>/<env-prefix>/data/tpcds/sf1"   # edit: where hdfs dfs -put landed the data
LOCAL_DIR   = "/home/cdsw/tpcds"                              # one sub-directory per table

tables = sorted(d for d in os.listdir(LOCAL_DIR) if os.path.isdir(os.path.join(LOCAL_DIR, d)))

with open("/home/cdsw/stage.sql", "w") as f:
    f.write("CREATE DATABASE IF NOT EXISTS tpcds_stage;\n")
    for t in tables:
        f.write(
            f"CREATE EXTERNAL TABLE IF NOT EXISTS tpcds_stage.{t}\n"
            f"  LIKE PARQUET '{BUCKET_PATH}/{t}/data.parquet'\n"
            f"  STORED AS PARQUET\n"
            f"  LOCATION '{BUCKET_PATH}/{t}/';\n"
        )

with open("/home/cdsw/iceberg.sql", "w") as f:
    f.write("CREATE DATABASE IF NOT EXISTS tpcds;\n")
    for t in tables:
        f.write(
            f"CREATE TABLE IF NOT EXISTS tpcds.{t} STORED AS ICEBERG\n"
            f"  AS SELECT * FROM tpcds_stage.{t};\n"
            f"COMPUTE STATS tpcds.{t};\n"
        )

print(f"{len(tables)} tables:", ", ".join(tables))
