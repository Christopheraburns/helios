SELECT
  household_demographics.hd_buy_potential AS hd_buy_potential,
  income_band.ib_lower_bound AS ib_lower_bound,
  SUM(store_sales.ss_net_paid) AS store_sales_revenue
FROM tpcds.store_sales AS store_sales
INNER JOIN tpcds.customer_demographics AS customer_demographics
  ON store_sales.ss_cdemo_sk = customer_demographics.cd_demo_sk
INNER JOIN tpcds.household_demographics AS household_demographics
  ON store_sales.ss_hdemo_sk = household_demographics.hd_demo_sk
INNER JOIN tpcds.income_band AS income_band
  ON household_demographics.hd_income_band_sk = income_band.ib_income_band_sk
WHERE
  customer_demographics.cd_gender = 'F'
GROUP BY
  household_demographics.hd_buy_potential,
  income_band.ib_lower_bound
