CREATE DATABASE IF NOT EXISTS tpcds_stage;
CREATE EXTERNAL TABLE IF NOT EXISTS tpcds_stage.call_center
  LIKE PARQUET 's3a://applied-ai-buk-d5eff1ab/data/tpcds/sf1/call_center/data.parquet'
  STORED AS PARQUET
  LOCATION 's3a://applied-ai-buk-d5eff1ab/data/tpcds/sf1/call_center/';
CREATE EXTERNAL TABLE IF NOT EXISTS tpcds_stage.catalog_page
  LIKE PARQUET 's3a://applied-ai-buk-d5eff1ab/data/tpcds/sf1/catalog_page/data.parquet'
  STORED AS PARQUET
  LOCATION 's3a://applied-ai-buk-d5eff1ab/data/tpcds/sf1/catalog_page/';
CREATE EXTERNAL TABLE IF NOT EXISTS tpcds_stage.catalog_returns
  LIKE PARQUET 's3a://applied-ai-buk-d5eff1ab/data/tpcds/sf1/catalog_returns/data.parquet'
  STORED AS PARQUET
  LOCATION 's3a://applied-ai-buk-d5eff1ab/data/tpcds/sf1/catalog_returns/';
CREATE EXTERNAL TABLE IF NOT EXISTS tpcds_stage.catalog_sales
  LIKE PARQUET 's3a://applied-ai-buk-d5eff1ab/data/tpcds/sf1/catalog_sales/data.parquet'
  STORED AS PARQUET
  LOCATION 's3a://applied-ai-buk-d5eff1ab/data/tpcds/sf1/catalog_sales/';
CREATE EXTERNAL TABLE IF NOT EXISTS tpcds_stage.customer
  LIKE PARQUET 's3a://applied-ai-buk-d5eff1ab/data/tpcds/sf1/customer/data.parquet'
  STORED AS PARQUET
  LOCATION 's3a://applied-ai-buk-d5eff1ab/data/tpcds/sf1/customer/';
CREATE EXTERNAL TABLE IF NOT EXISTS tpcds_stage.customer_address
  LIKE PARQUET 's3a://applied-ai-buk-d5eff1ab/data/tpcds/sf1/customer_address/data.parquet'
  STORED AS PARQUET
  LOCATION 's3a://applied-ai-buk-d5eff1ab/data/tpcds/sf1/customer_address/';
CREATE EXTERNAL TABLE IF NOT EXISTS tpcds_stage.customer_demographics
  LIKE PARQUET 's3a://applied-ai-buk-d5eff1ab/data/tpcds/sf1/customer_demographics/data.parquet'
  STORED AS PARQUET
  LOCATION 's3a://applied-ai-buk-d5eff1ab/data/tpcds/sf1/customer_demographics/';
CREATE EXTERNAL TABLE IF NOT EXISTS tpcds_stage.date_dim
  LIKE PARQUET 's3a://applied-ai-buk-d5eff1ab/data/tpcds/sf1/date_dim/data.parquet'
  STORED AS PARQUET
  LOCATION 's3a://applied-ai-buk-d5eff1ab/data/tpcds/sf1/date_dim/';
CREATE EXTERNAL TABLE IF NOT EXISTS tpcds_stage.household_demographics
  LIKE PARQUET 's3a://applied-ai-buk-d5eff1ab/data/tpcds/sf1/household_demographics/data.parquet'
  STORED AS PARQUET
  LOCATION 's3a://applied-ai-buk-d5eff1ab/data/tpcds/sf1/household_demographics/';
CREATE EXTERNAL TABLE IF NOT EXISTS tpcds_stage.income_band
  LIKE PARQUET 's3a://applied-ai-buk-d5eff1ab/data/tpcds/sf1/income_band/data.parquet'
  STORED AS PARQUET
  LOCATION 's3a://applied-ai-buk-d5eff1ab/data/tpcds/sf1/income_band/';
CREATE EXTERNAL TABLE IF NOT EXISTS tpcds_stage.inventory
  LIKE PARQUET 's3a://applied-ai-buk-d5eff1ab/data/tpcds/sf1/inventory/data.parquet'
  STORED AS PARQUET
  LOCATION 's3a://applied-ai-buk-d5eff1ab/data/tpcds/sf1/inventory/';
CREATE EXTERNAL TABLE IF NOT EXISTS tpcds_stage.item
  LIKE PARQUET 's3a://applied-ai-buk-d5eff1ab/data/tpcds/sf1/item/data.parquet'
  STORED AS PARQUET
  LOCATION 's3a://applied-ai-buk-d5eff1ab/data/tpcds/sf1/item/';
CREATE EXTERNAL TABLE IF NOT EXISTS tpcds_stage.promotion
  LIKE PARQUET 's3a://applied-ai-buk-d5eff1ab/data/tpcds/sf1/promotion/data.parquet'
  STORED AS PARQUET
  LOCATION 's3a://applied-ai-buk-d5eff1ab/data/tpcds/sf1/promotion/';
CREATE EXTERNAL TABLE IF NOT EXISTS tpcds_stage.reason
  LIKE PARQUET 's3a://applied-ai-buk-d5eff1ab/data/tpcds/sf1/reason/data.parquet'
  STORED AS PARQUET
  LOCATION 's3a://applied-ai-buk-d5eff1ab/data/tpcds/sf1/reason/';
CREATE EXTERNAL TABLE IF NOT EXISTS tpcds_stage.ship_mode
  LIKE PARQUET 's3a://applied-ai-buk-d5eff1ab/data/tpcds/sf1/ship_mode/data.parquet'
  STORED AS PARQUET
  LOCATION 's3a://applied-ai-buk-d5eff1ab/data/tpcds/sf1/ship_mode/';
CREATE EXTERNAL TABLE IF NOT EXISTS tpcds_stage.store
  LIKE PARQUET 's3a://applied-ai-buk-d5eff1ab/data/tpcds/sf1/store/data.parquet'
  STORED AS PARQUET
  LOCATION 's3a://applied-ai-buk-d5eff1ab/data/tpcds/sf1/store/';
CREATE EXTERNAL TABLE IF NOT EXISTS tpcds_stage.store_returns
  LIKE PARQUET 's3a://applied-ai-buk-d5eff1ab/data/tpcds/sf1/store_returns/data.parquet'
  STORED AS PARQUET
  LOCATION 's3a://applied-ai-buk-d5eff1ab/data/tpcds/sf1/store_returns/';
CREATE EXTERNAL TABLE IF NOT EXISTS tpcds_stage.store_sales
  LIKE PARQUET 's3a://applied-ai-buk-d5eff1ab/data/tpcds/sf1/store_sales/data.parquet'
  STORED AS PARQUET
  LOCATION 's3a://applied-ai-buk-d5eff1ab/data/tpcds/sf1/store_sales/';
CREATE EXTERNAL TABLE IF NOT EXISTS tpcds_stage.time_dim
  LIKE PARQUET 's3a://applied-ai-buk-d5eff1ab/data/tpcds/sf1/time_dim/data.parquet'
  STORED AS PARQUET
  LOCATION 's3a://applied-ai-buk-d5eff1ab/data/tpcds/sf1/time_dim/';
CREATE EXTERNAL TABLE IF NOT EXISTS tpcds_stage.warehouse
  LIKE PARQUET 's3a://applied-ai-buk-d5eff1ab/data/tpcds/sf1/warehouse/data.parquet'
  STORED AS PARQUET
  LOCATION 's3a://applied-ai-buk-d5eff1ab/data/tpcds/sf1/warehouse/';
CREATE EXTERNAL TABLE IF NOT EXISTS tpcds_stage.web_page
  LIKE PARQUET 's3a://applied-ai-buk-d5eff1ab/data/tpcds/sf1/web_page/data.parquet'
  STORED AS PARQUET
  LOCATION 's3a://applied-ai-buk-d5eff1ab/data/tpcds/sf1/web_page/';
CREATE EXTERNAL TABLE IF NOT EXISTS tpcds_stage.web_returns
  LIKE PARQUET 's3a://applied-ai-buk-d5eff1ab/data/tpcds/sf1/web_returns/data.parquet'
  STORED AS PARQUET
  LOCATION 's3a://applied-ai-buk-d5eff1ab/data/tpcds/sf1/web_returns/';
CREATE EXTERNAL TABLE IF NOT EXISTS tpcds_stage.web_sales
  LIKE PARQUET 's3a://applied-ai-buk-d5eff1ab/data/tpcds/sf1/web_sales/data.parquet'
  STORED AS PARQUET
  LOCATION 's3a://applied-ai-buk-d5eff1ab/data/tpcds/sf1/web_sales/';
CREATE EXTERNAL TABLE IF NOT EXISTS tpcds_stage.web_site
  LIKE PARQUET 's3a://applied-ai-buk-d5eff1ab/data/tpcds/sf1/web_site/data.parquet'
  STORED AS PARQUET
  LOCATION 's3a://applied-ai-buk-d5eff1ab/data/tpcds/sf1/web_site/';
