SELECT
  date_dim.d_year AS d_year,
  SUM(catalog_sales.cs_net_paid) AS catalog_sales_revenue
FROM tpcds.catalog_sales AS catalog_sales
INNER JOIN tpcds.date_dim AS date_dim
  ON catalog_sales.cs_ship_date_sk = date_dim.d_date_sk
GROUP BY
  date_dim.d_year
