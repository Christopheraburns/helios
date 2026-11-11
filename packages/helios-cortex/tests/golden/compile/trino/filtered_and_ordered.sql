SELECT
  date_dim.d_year AS d_year,
  store.s_state AS s_state,
  SUM(store_sales.ss_net_paid) AS store_sales_revenue
FROM tpcds.store_sales AS store_sales
INNER JOIN tpcds.date_dim AS date_dim
  ON store_sales.ss_sold_date_sk = date_dim.d_date_sk
INNER JOIN tpcds.item AS item
  ON store_sales.ss_item_sk = item.i_item_sk
INNER JOIN tpcds.store AS store
  ON store_sales.ss_store_sk = store.s_store_sk
WHERE
  date_dim.d_year = 2001 AND item.i_category IN ('Music', 'Books')
GROUP BY
  date_dim.d_year,
  store.s_state
ORDER BY
  store_sales_revenue DESC
LIMIT 100
