SELECT
  date_dim.d_year AS d_year,
  SUM(store_sales.ss_net_paid) AS store_sales_revenue
FROM tpcds.store_sales AS store_sales
INNER JOIN tpcds.date_dim AS date_dim
  ON store_sales.ss_sold_date_sk = date_dim.d_date_sk
GROUP BY
  date_dim.d_year
