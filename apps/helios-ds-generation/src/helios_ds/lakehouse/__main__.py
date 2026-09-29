"""Print the Impala DDL: python -m helios_ds.lakehouse > schemas/lakehouse_impala.sql"""

from .tables import impala_ddl

print(impala_ddl(), end="")
