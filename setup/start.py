import duckdb
import os


con = duckdb.connect()
con.sql("INSTALL tpcds; load tpcds; CALL dsdgen(sf=1);")
tables = [r[0] for r in con.sql("SHOW TABLES").fetchall()]
for t in tables:
  os.makedirs(f"/home/cdsw/tpcds/{t}", exist_ok=True)
  con.sql(f"COPY {t} TO '/home/cdsw/tpcds/{t}/data.parquet' (FORMAT PARQUET)")
  
  
