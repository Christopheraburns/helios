CREATE DATABASE IF NOT EXISTS tpcds;
CREATE TABLE IF NOT EXISTS tpcds.call_center STORED AS ICEBERG
  AS SELECT * FROM tpcds_stage.call_center;
COMPUTE STATS tpcds.call_center;
CREATE TABLE IF NOT EXISTS tpcds.catalog_page STORED AS ICEBERG
  AS SELECT * FROM tpcds_stage.catalog_page;
COMPUTE STATS tpcds.catalog_page;
CREATE TABLE IF NOT EXISTS tpcds.catalog_returns STORED AS ICEBERG
  AS SELECT * FROM tpcds_stage.catalog_returns;
COMPUTE STATS tpcds.catalog_returns;
CREATE TABLE IF NOT EXISTS tpcds.catalog_sales STORED AS ICEBERG
  AS SELECT * FROM tpcds_stage.catalog_sales;
COMPUTE STATS tpcds.catalog_sales;
CREATE TABLE IF NOT EXISTS tpcds.customer STORED AS ICEBERG
  AS SELECT * FROM tpcds_stage.customer;
COMPUTE STATS tpcds.customer;
CREATE TABLE IF NOT EXISTS tpcds.customer_address STORED AS ICEBERG
  AS SELECT * FROM tpcds_stage.customer_address;
COMPUTE STATS tpcds.customer_address;
CREATE TABLE IF NOT EXISTS tpcds.customer_demographics STORED AS ICEBERG
  AS SELECT * FROM tpcds_stage.customer_demographics;
COMPUTE STATS tpcds.customer_demographics;
CREATE TABLE IF NOT EXISTS tpcds.date_dim STORED AS ICEBERG
  AS SELECT * FROM tpcds_stage.date_dim;
COMPUTE STATS tpcds.date_dim;
CREATE TABLE IF NOT EXISTS tpcds.household_demographics STORED AS ICEBERG
  AS SELECT * FROM tpcds_stage.household_demographics;
COMPUTE STATS tpcds.household_demographics;
CREATE TABLE IF NOT EXISTS tpcds.income_band STORED AS ICEBERG
  AS SELECT * FROM tpcds_stage.income_band;
COMPUTE STATS tpcds.income_band;
CREATE TABLE IF NOT EXISTS tpcds.inventory STORED AS ICEBERG
  AS SELECT * FROM tpcds_stage.inventory;
COMPUTE STATS tpcds.inventory;
CREATE TABLE IF NOT EXISTS tpcds.item STORED AS ICEBERG
  AS SELECT * FROM tpcds_stage.item;
COMPUTE STATS tpcds.item;
CREATE TABLE IF NOT EXISTS tpcds.promotion STORED AS ICEBERG
  AS SELECT * FROM tpcds_stage.promotion;
COMPUTE STATS tpcds.promotion;
CREATE TABLE IF NOT EXISTS tpcds.reason STORED AS ICEBERG
  AS SELECT * FROM tpcds_stage.reason;
COMPUTE STATS tpcds.reason;
CREATE TABLE IF NOT EXISTS tpcds.ship_mode STORED AS ICEBERG
  AS SELECT * FROM tpcds_stage.ship_mode;
COMPUTE STATS tpcds.ship_mode;
CREATE TABLE IF NOT EXISTS tpcds.store STORED AS ICEBERG
  AS SELECT * FROM tpcds_stage.store;
COMPUTE STATS tpcds.store;
CREATE TABLE IF NOT EXISTS tpcds.store_returns STORED AS ICEBERG
  AS SELECT * FROM tpcds_stage.store_returns;
COMPUTE STATS tpcds.store_returns;
CREATE TABLE IF NOT EXISTS tpcds.store_sales STORED AS ICEBERG
  AS SELECT * FROM tpcds_stage.store_sales;
COMPUTE STATS tpcds.store_sales;
CREATE TABLE IF NOT EXISTS tpcds.time_dim STORED AS ICEBERG
  AS SELECT * FROM tpcds_stage.time_dim;
COMPUTE STATS tpcds.time_dim;
CREATE TABLE IF NOT EXISTS tpcds.warehouse STORED AS ICEBERG
  AS SELECT * FROM tpcds_stage.warehouse;
COMPUTE STATS tpcds.warehouse;
CREATE TABLE IF NOT EXISTS tpcds.web_page STORED AS ICEBERG
  AS SELECT * FROM tpcds_stage.web_page;
COMPUTE STATS tpcds.web_page;
CREATE TABLE IF NOT EXISTS tpcds.web_returns STORED AS ICEBERG
  AS SELECT * FROM tpcds_stage.web_returns;
COMPUTE STATS tpcds.web_returns;
CREATE TABLE IF NOT EXISTS tpcds.web_sales STORED AS ICEBERG
  AS SELECT * FROM tpcds_stage.web_sales;
COMPUTE STATS tpcds.web_sales;
CREATE TABLE IF NOT EXISTS tpcds.web_site STORED AS ICEBERG
  AS SELECT * FROM tpcds_stage.web_site;
COMPUTE STATS tpcds.web_site;
