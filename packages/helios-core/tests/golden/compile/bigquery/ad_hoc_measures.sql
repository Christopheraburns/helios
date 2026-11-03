SELECT
  date_dim.d_year AS d_year,
  item.i_brand AS i_brand,
  SUM(store_sales.ss_ext_sales_price) AS sum_agg,
  COUNT(DISTINCT store_sales.ss_customer_sk) AS customers
FROM tpcds.store_sales AS store_sales
INNER JOIN tpcds.date_dim AS date_dim
  ON store_sales.ss_sold_date_sk = date_dim.d_date_sk
INNER JOIN tpcds.item AS item
  ON store_sales.ss_item_sk = item.i_item_sk
GROUP BY
  date_dim.d_year,
  item.i_brand
LIMIT 10
