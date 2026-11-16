"""
build_glossary.py
Generates an Apache Atlas glossary for the TPC-DS retail warehouse.

Outputs (same directory as this script):
  tpcds_glossary_full.csv   - all terms, Atlas bulk-import format
  tpcds_glossary_seed.csv   - small "cold start" subset for discovery-agent experiments
  tpcds_term_columns.csv    - term -> table.column assignments (used by assign_terms.py)

Definitions follow the TPC-DS specification's table/column semantics. Where a
formula is stated it is the dsdgen pricing relationship; the two flagged as
"verify" should be checked against dsdgen's pricing.c before being promoted to
a governed metric.
"""
import csv, os

GLOSSARY = "TPCDS Retail"
OUT = os.path.dirname(os.path.abspath(__file__))

SALES  = ["store_sales", "catalog_sales", "web_sales"]
SPFX   = {"store_sales": "ss", "catalog_sales": "cs", "web_sales": "ws"}
RETS   = ["store_returns", "catalog_returns", "web_returns"]
RPFX   = {"store_returns": "sr", "catalog_returns": "cr", "web_returns": "wr"}

def sales(col):   return [f"{t}.{SPFX[t]}_{col}" for t in SALES]
def rets(col):    return [f"{t}.{RPFX[t]}_{col}" for t in RETS]

# (name, short, long, abbreviation, examples, synonyms, is_a, see_also, tier, columns)
# tier: "seed" terms are included in the small cold-start glossary; "full" only in the full one.
T = []
def term(name, short, long="", abbr="", examples="", syn="", isa="", see="", tier="full", cols=None):
    T.append(dict(name=name, short=short, long=long, abbr=abbr, examples=examples,
                  syn=syn, isa=isa, see=see, tier=tier, cols=cols or []))

# ---------------------------------------------------------------- parent / concept terms
term("Sales Channel", "One of the three ways the retailer sells goods: physical store, printed catalog, or web site.",
     "Every sales and returns fact table belongs to exactly one channel. Channel is implied by the table, not stored as a column.",
     examples="Store; Catalog; Web", tier="seed")
term("Sales Transaction", "A line item recording the sale of a quantity of one item to one customer on one date.",
     "One row per item per ticket/order. Store sales are grouped by Ticket Number; catalog and web sales by Order Number.",
     syn="Sale|Sales Line", see="Sales Channel|Return Transaction", tier="seed")
term("Return Transaction", "A line item recording a customer returning a quantity of one item previously sold.",
     "Returns reference the original sale via Ticket Number or Order Number plus Item. Not every sale has a return.",
     syn="Return|Merchandise Return", see="Sales Transaction|Return Reason", tier="seed")
term("Sales Measure", "A numeric fact recorded on a sales transaction line.", isa="", tier="full")
term("Return Measure", "A numeric fact recorded on a return transaction line.", tier="full")
term("Dimension", "A descriptive entity used to slice facts: who, what, where, when.", tier="full")
term("Surrogate Key", "System-generated integer identifier used to join fact tables to dimension tables.",
     "Columns ending in _sk. Never meaningful to the business; use the corresponding Business Key (_id) for reporting.",
     abbr="SK", examples="ss_customer_sk -> customer.c_customer_sk", see="Business Key", tier="seed")
term("Business Key", "Stable, human-readable identifier for a dimension member that survives Type-2 history changes.",
     "Columns ending in _id (16-character string). Several surrogate keys can share one business key when the entity has history.",
     abbr="ID", examples="AAAAAAAABAAAAAAA", see="Surrogate Key|Slowly Changing Dimension", tier="seed")
term("Slowly Changing Dimension", "A dimension that keeps history: each version is a separate row with a record start and end date.",
     "Item, Store, Call Center, Web Site and Web Page are Type-2. Current version has a null record end date.",
     abbr="SCD2", see="Business Key",
     cols=["item.i_rec_start_date","item.i_rec_end_date","store.s_rec_start_date","store.s_rec_end_date",
           "call_center.cc_rec_start_date","call_center.cc_rec_end_date","web_site.web_rec_start_date",
           "web_site.web_rec_end_date","web_page.wp_rec_start_date","web_page.wp_rec_end_date"])

# ---------------------------------------------------------------- customer & demographics
term("Customer", "A person who has purchased from any channel.",
     "One row per customer. Links to current demographics and address; historical demographics at time of sale are on the fact rows.",
     syn="Shopper|Buyer", isa="Dimension", tier="seed",
     cols=["customer.c_customer_sk","customer.c_customer_id","store_sales.ss_customer_sk","store_returns.sr_customer_sk"])
term("Bill-To Customer", "On catalog and web orders, the customer who was billed for the order.",
     "May differ from the Ship-To Customer (gifts, business orders).", see="Ship-To Customer",
     cols=["catalog_sales.cs_bill_customer_sk","web_sales.ws_bill_customer_sk"])
term("Ship-To Customer", "On catalog and web orders, the customer the goods were shipped to.", see="Bill-To Customer",
     cols=["catalog_sales.cs_ship_customer_sk","web_sales.ws_ship_customer_sk"])
term("Refunded Customer", "On catalog and web returns, the customer who received the refund (the original buyer).",
     see="Returning Customer", cols=["catalog_returns.cr_refunded_customer_sk","web_returns.wr_refunded_customer_sk"])
term("Returning Customer", "On catalog and web returns, the customer who physically returned the goods.",
     see="Refunded Customer", cols=["catalog_returns.cr_returning_customer_sk","web_returns.wr_returning_customer_sk"])
term("Preferred Customer", "Flag marking customers enrolled in the preferred/loyalty program.", examples="Y; N",
     cols=["customer.c_preferred_cust_flag"])
term("Customer Name", "Salutation, first and last name of the customer.",
     cols=["customer.c_salutation","customer.c_first_name","customer.c_last_name"])
term("Birth Date", "Customer date of birth, stored as separate day, month and year parts.",
     cols=["customer.c_birth_day","customer.c_birth_month","customer.c_birth_year"])
term("Birth Country", "Country in which the customer was born.", cols=["customer.c_birth_country"])
term("Customer Email", "Customer email address.", cols=["customer.c_email_address"])
term("Customer Login", "Customer web login name.", cols=["customer.c_login"])
term("First Sale Date", "Date of the customer's first purchase in any channel.", cols=["customer.c_first_sales_date_sk"])
term("First Ship-To Date", "Date of the first order shipped to the customer.", cols=["customer.c_first_shipto_date_sk"])
term("Last Review Date", "Date the customer record was last reviewed.", cols=["customer.c_last_review_date_sk"])
term("Customer Demographics", "Personal demographic profile: gender, marital status, education, credit rating, dependents.",
     "A shared lookup; many customers map to the same demographic row. Fact rows carry the demographics in effect at time of sale.",
     abbr="CDEMO", isa="Dimension", tier="seed",
     cols=["customer_demographics.cd_demo_sk","customer.c_current_cdemo_sk","store_sales.ss_cdemo_sk",
           "catalog_sales.cs_bill_cdemo_sk","catalog_sales.cs_ship_cdemo_sk","web_sales.ws_bill_cdemo_sk",
           "web_sales.ws_ship_cdemo_sk","store_returns.sr_cdemo_sk"])
term("Gender", "Customer gender.", examples="M; F", cols=["customer_demographics.cd_gender"])
term("Marital Status", "Customer marital status code.", examples="M married; S single; D divorced; W widowed; U unknown",
     cols=["customer_demographics.cd_marital_status"])
term("Education Status", "Highest education level attained.",
     examples="Primary; Secondary; College; 2 yr Degree; 4 yr Degree; Advanced Degree; Unknown",
     cols=["customer_demographics.cd_education_status"])
term("Purchase Estimate", "Estimated annual purchase amount band for the customer, in whole currency units.",
     cols=["customer_demographics.cd_purchase_estimate"])
term("Credit Rating", "Credit risk classification of the customer.", examples="Good; Low Risk; High Risk; Unknown",
     cols=["customer_demographics.cd_credit_rating"])
term("Dependent Count", "Number of dependents in the customer's household.",
     cols=["customer_demographics.cd_dep_count","household_demographics.hd_dep_count"])
term("Employed Dependent Count", "Number of dependents who are employed.", cols=["customer_demographics.cd_dep_employed_count"])
term("College Dependent Count", "Number of dependents attending college.", cols=["customer_demographics.cd_dep_college_count"])
term("Household Demographics", "Household-level profile: income band, buying potential, dependents, vehicles.",
     abbr="HDEMO", isa="Dimension", tier="seed",
     cols=["household_demographics.hd_demo_sk","customer.c_current_hdemo_sk","store_sales.ss_hdemo_sk",
           "catalog_sales.cs_bill_hdemo_sk","catalog_sales.cs_ship_hdemo_sk","web_sales.ws_bill_hdemo_sk",
           "web_sales.ws_ship_hdemo_sk","store_returns.sr_hdemo_sk"])
term("Buy Potential", "Estimated household annual spending band.",
     examples="0-500; 501-1000; 1001-5000; 5001-10000; >10000; Unknown", cols=["household_demographics.hd_buy_potential"])
term("Vehicle Count", "Number of vehicles in the household.", cols=["household_demographics.hd_vehicle_count"])
term("Income Band", "Household annual income range with lower and upper bound.", isa="Dimension",
     cols=["income_band.ib_income_band_sk","income_band.ib_lower_bound","income_band.ib_upper_bound",
           "household_demographics.hd_income_band_sk"])
term("Customer Address", "Postal address; used for the customer's current address and for the address on each sale.",
     "Fact rows reference the address used at time of sale, which may differ from the customer's current address.",
     isa="Dimension", tier="seed",
     cols=["customer_address.ca_address_sk","customer_address.ca_address_id","customer.c_current_addr_sk",
           "store_sales.ss_addr_sk","catalog_sales.cs_bill_addr_sk","catalog_sales.cs_ship_addr_sk",
           "web_sales.ws_bill_addr_sk","web_sales.ws_ship_addr_sk","store_returns.sr_addr_sk"])
term("Location Type", "Type of dwelling at the address.", examples="apartment; condo; single family",
     cols=["customer_address.ca_location_type"])
term("Geography", "City, county, state, zip and country attributes shared by all address-bearing entities.",
     cols=["customer_address.ca_city","customer_address.ca_county","customer_address.ca_state","customer_address.ca_zip",
           "customer_address.ca_country","store.s_city","store.s_county","store.s_state","store.s_zip","store.s_country",
           "warehouse.w_city","warehouse.w_county","warehouse.w_state","warehouse.w_zip","warehouse.w_country",
           "call_center.cc_city","call_center.cc_county","call_center.cc_state","call_center.cc_zip","call_center.cc_country",
           "web_site.web_city","web_site.web_county","web_site.web_state","web_site.web_zip","web_site.web_country"])
term("GMT Offset", "Time-zone offset from GMT, in hours, for a location.",
     cols=["customer_address.ca_gmt_offset","store.s_gmt_offset","warehouse.w_gmt_offset",
           "call_center.cc_gmt_offset","web_site.web_gmt_offset"])

# ---------------------------------------------------------------- product
term("Item", "A product the retailer sells. Type-2: price and attributes are versioned over time.",
     "Join facts on i_item_sk to get the item version in effect at the time of sale; use i_item_id to group across versions.",
     syn="Product|SKU", isa="Dimension|Slowly Changing Dimension", tier="seed",
     cols=["item.i_item_sk","item.i_item_id"]+sales("item_sk")+rets("item_sk")+["inventory.inv_item_sk","promotion.p_item_sk"])
term("Product Name", "Display name of the item.", cols=["item.i_product_name","item.i_item_desc"])
term("Current Price", "Current list price of the item version.", cols=["item.i_current_price"])
term("Item Wholesale Cost", "Standard per-unit cost the retailer pays for the item.", cols=["item.i_wholesale_cost"])
term("Brand", "Brand of the item.", cols=["item.i_brand","item.i_brand_id"])
term("Category", "Top level of the product hierarchy.",
     examples="Books; Children; Electronics; Home; Jewelry; Men; Music; Shoes; Sports; Women",
     see="Class|Brand", tier="seed", cols=["item.i_category","item.i_category_id"])
term("Class", "Second level of the product hierarchy, within a Category.", see="Category",
     cols=["item.i_class","item.i_class_id"])
term("Manufacturer", "Manufacturer of the item.", cols=["item.i_manufact","item.i_manufact_id"])
term("Product Manager", "Identifier of the manager responsible for the item.", cols=["item.i_manager_id"])
term("Item Attributes", "Physical attributes: size, color, units, container, formulation.",
     cols=["item.i_size","item.i_color","item.i_units","item.i_container","item.i_formulation"])

# ---------------------------------------------------------------- channel dimensions
term("Store", "A physical retail location. Type-2.", isa="Dimension|Slowly Changing Dimension", tier="seed",
     cols=["store.s_store_sk","store.s_store_id","store_sales.ss_store_sk","store_returns.sr_store_sk"])
term("Store Name", "Name of the store.", cols=["store.s_store_name"])
term("Store Manager", "Name of the store manager.", cols=["store.s_manager"])
term("Store Size", "Floor space in square feet and number of employees.", cols=["store.s_floor_space","store.s_number_employees"])
term("Store Hours", "Operating hours description.", cols=["store.s_hours"])
term("Store Closed Date", "Date the store closed; null while open.", cols=["store.s_closed_date_sk"])
term("Market", "Marketing region an operating unit belongs to.",
     cols=["store.s_market_id","store.s_market_desc","store.s_market_manager","store.s_geography_class",
           "call_center.cc_mkt_id","call_center.cc_mkt_class","call_center.cc_mkt_desc","call_center.cc_market_manager",
           "web_site.web_mkt_id","web_site.web_mkt_class","web_site.web_mkt_desc","web_site.web_market_manager"])
term("Division", "Organizational division an operating unit belongs to.",
     cols=["store.s_division_id","store.s_division_name","call_center.cc_division","call_center.cc_division_name"])
term("Company", "Legal company an operating unit belongs to.",
     cols=["store.s_company_id","store.s_company_name","call_center.cc_company","call_center.cc_company_name",
           "web_site.web_company_id","web_site.web_company_name"])
term("Tax Percentage", "Sales tax rate applied at a store, call center or web site.",
     cols=["store.s_tax_percentage","call_center.cc_tax_percentage","web_site.web_tax_percentage"])
term("Call Center", "A facility that takes catalog orders and returns by phone. Type-2.",
     isa="Dimension|Slowly Changing Dimension", tier="seed",
     cols=["call_center.cc_call_center_sk","call_center.cc_call_center_id","catalog_sales.cs_call_center_sk",
           "catalog_returns.cr_call_center_sk"])
term("Call Center Attributes", "Name, class, size, hours and manager of a call center.",
     cols=["call_center.cc_name","call_center.cc_class","call_center.cc_employees","call_center.cc_sq_ft",
           "call_center.cc_hours","call_center.cc_manager","call_center.cc_open_date_sk","call_center.cc_closed_date_sk"])
term("Catalog Page", "A page in a printed catalog from which an item was ordered.", isa="Dimension", tier="seed",
     cols=["catalog_page.cp_catalog_page_sk","catalog_page.cp_catalog_page_id","catalog_sales.cs_catalog_page_sk",
           "catalog_returns.cr_catalog_page_sk"])
term("Catalog Page Attributes", "Catalog number, page number, department, type and validity dates.",
     cols=["catalog_page.cp_catalog_number","catalog_page.cp_catalog_page_number","catalog_page.cp_department",
           "catalog_page.cp_type","catalog_page.cp_description","catalog_page.cp_start_date_sk","catalog_page.cp_end_date_sk"])
term("Web Site", "A retailer web property through which orders are placed. Type-2.",
     isa="Dimension|Slowly Changing Dimension", tier="seed",
     cols=["web_site.web_site_sk","web_site.web_site_id","web_sales.ws_web_site_sk"])
term("Web Site Attributes", "Name, class, manager and open/close dates of a web site.",
     cols=["web_site.web_name","web_site.web_class","web_site.web_manager","web_site.web_open_date_sk","web_site.web_close_date_sk"])
term("Web Page", "A specific page on a web site from which an order or return originated. Type-2.",
     isa="Dimension|Slowly Changing Dimension",
     cols=["web_page.wp_web_page_sk","web_page.wp_web_page_id","web_sales.ws_web_page_sk","web_returns.wr_web_page_sk"])
term("Web Page Attributes", "URL, type, content counts and creation/access dates of a web page.",
     cols=["web_page.wp_url","web_page.wp_type","web_page.wp_char_count","web_page.wp_link_count","web_page.wp_image_count",
           "web_page.wp_max_ad_count","web_page.wp_autogen_flag","web_page.wp_creation_date_sk","web_page.wp_access_date_sk",
           "web_page.wp_customer_sk"])
term("Warehouse", "A distribution facility that holds inventory and ships catalog and web orders.", isa="Dimension", tier="seed",
     cols=["warehouse.w_warehouse_sk","warehouse.w_warehouse_id","warehouse.w_warehouse_name","warehouse.w_warehouse_sq_ft",
           "catalog_sales.cs_warehouse_sk","web_sales.ws_warehouse_sk","catalog_returns.cr_warehouse_sk","inventory.inv_warehouse_sk"])
term("Ship Mode", "Shipping service level and carrier used for a catalog or web order.", isa="Dimension",
     cols=["ship_mode.sm_ship_mode_sk","ship_mode.sm_ship_mode_id","catalog_sales.cs_ship_mode_sk","web_sales.ws_ship_mode_sk",
           "catalog_returns.cr_ship_mode_sk"])
term("Ship Mode Type", "Service level of the shipment.", examples="EXPRESS; NEXT DAY; OVERNIGHT; REGULAR; TWO DAY; LIBRARY",
     cols=["ship_mode.sm_type","ship_mode.sm_code"])
term("Carrier", "Shipping company used.", cols=["ship_mode.sm_carrier","ship_mode.sm_contract"])
term("Promotion", "A marketing campaign offering a discount on an item over a date range.", isa="Dimension", tier="seed",
     cols=["promotion.p_promo_sk","promotion.p_promo_id","promotion.p_promo_name"]+sales("promo_sk"))
term("Promotion Period", "Start and end dates of the promotion.", cols=["promotion.p_start_date_sk","promotion.p_end_date_sk"])
term("Promotion Cost", "Amount spent to run the promotion.", cols=["promotion.p_cost"])
term("Promotion Channel", "Marketing channels through which the promotion was communicated (Y/N flags).",
     examples="dmail; email; catalog; tv; radio; press; event; demo",
     cols=["promotion.p_channel_dmail","promotion.p_channel_email","promotion.p_channel_catalog","promotion.p_channel_tv",
           "promotion.p_channel_radio","promotion.p_channel_press","promotion.p_channel_event","promotion.p_channel_demo",
           "promotion.p_channel_details"])
term("Promotion Purpose", "Business purpose and response target of the promotion.",
     cols=["promotion.p_purpose","promotion.p_response_target"])
term("Discount Active", "Whether the promotion's discount was in effect.", examples="Y; N", cols=["promotion.p_discount_active"])
term("Return Reason", "Reason code given by the customer for a return.", isa="Dimension", tier="seed",
     cols=["reason.r_reason_sk","reason.r_reason_id","reason.r_reason_desc"]+rets("reason_sk"))

# ---------------------------------------------------------------- time
term("Calendar Date", "One row per calendar day with fiscal and calendar attributes.",
     "All facts reference dates by surrogate key. Sales use the sold date; returns use the returned date; catalog/web also carry a ship date.",
     syn="Date|Day", isa="Dimension", tier="seed",
     cols=["date_dim.d_date_sk","date_dim.d_date_id","date_dim.d_date"])
term("Sold Date", "Date the sale was made.", cols=sales("sold_date_sk"))
term("Ship Date", "Date a catalog or web order was shipped.", cols=["catalog_sales.cs_ship_date_sk","web_sales.ws_ship_date_sk"])
term("Returned Date", "Date the return was processed.", cols=rets("returned_date_sk"))
term("Calendar Year", "Calendar year of the date.", cols=["date_dim.d_year"])
term("Fiscal Year", "Fiscal year of the date.", cols=["date_dim.d_fy_year","date_dim.d_fy_quarter_seq","date_dim.d_fy_week_seq"])
term("Quarter", "Calendar quarter (1-4) and quarter name.", cols=["date_dim.d_qoy","date_dim.d_quarter_name","date_dim.d_quarter_seq"])
term("Month", "Month of year and a monotonically increasing month sequence used for period arithmetic.",
     cols=["date_dim.d_moy","date_dim.d_month_seq","date_dim.d_first_dom","date_dim.d_last_dom"])
term("Week", "Monotonically increasing week sequence.", cols=["date_dim.d_week_seq"])
term("Day of Week", "Day of week number and name.", cols=["date_dim.d_dow","date_dim.d_day_name","date_dim.d_dom"])
term("Holiday", "Whether the date is a holiday, and whether it follows one.", examples="Y; N",
     cols=["date_dim.d_holiday","date_dim.d_following_holiday"])
term("Weekend", "Whether the date falls on a weekend.", examples="Y; N", cols=["date_dim.d_weekend"])
term("Same Day Last Year", "Surrogate key of the equivalent day one year / one quarter earlier, for period-over-period comparison.",
     cols=["date_dim.d_same_day_ly","date_dim.d_same_day_lq"])
term("Current Period Flags", "Flags marking the current day, week, month, quarter and year.",
     cols=["date_dim.d_current_day","date_dim.d_current_week","date_dim.d_current_month","date_dim.d_current_quarter",
           "date_dim.d_current_year"])
term("Time of Day", "One row per second of the day with hour, minute, shift and meal-time attributes.", isa="Dimension",
     cols=["time_dim.t_time_sk","time_dim.t_time_id","time_dim.t_time","time_dim.t_hour","time_dim.t_minute","time_dim.t_second",
           "time_dim.t_am_pm"]+sales("sold_time_sk")+["store_returns.sr_return_time_sk","catalog_returns.cr_returned_time_sk",
           "web_returns.wr_returned_time_sk"])
term("Shift", "Work shift in which the time falls.", examples="first; second; third", cols=["time_dim.t_shift","time_dim.t_sub_shift"])
term("Meal Time", "Meal period in which the time falls.", examples="breakfast; lunch; dinner", cols=["time_dim.t_meal_time"])

# ---------------------------------------------------------------- transaction identifiers
term("Ticket Number", "Identifier of a store sales receipt; groups the line items of one store visit.",
     "Not unique on its own: the natural key of a store sales line is (ticket number, item).",
     isa="Business Key", see="Order Number", tier="seed", cols=["store_sales.ss_ticket_number","store_returns.sr_ticket_number"])
term("Order Number", "Identifier of a catalog or web order; groups the line items of one order.",
     "Natural key of a line is (order number, item).", isa="Business Key", see="Ticket Number", tier="seed",
     cols=["catalog_sales.cs_order_number","web_sales.ws_order_number","catalog_returns.cr_order_number",
           "web_returns.wr_order_number"])

# ---------------------------------------------------------------- sales measures
term("Quantity Sold", "Number of units of the item on the sales line.", isa="Sales Measure", tier="seed", cols=sales("quantity"))
term("Unit Wholesale Cost", "Per-unit cost to the retailer at time of sale.", isa="Sales Measure", cols=sales("wholesale_cost"))
term("Unit List Price", "Per-unit list (undiscounted) price at time of sale.", isa="Sales Measure", cols=sales("list_price"))
term("Unit Sales Price", "Per-unit price actually charged before coupons.", isa="Sales Measure", cols=sales("sales_price"))
term("Extended Wholesale Cost", "Quantity Sold x Unit Wholesale Cost.", isa="Sales Measure", syn="COGS Line",
     cols=sales("ext_wholesale_cost"))
term("Extended List Price", "Quantity Sold x Unit List Price.", isa="Sales Measure", cols=sales("ext_list_price"))
term("Extended Sales Price", "Quantity Sold x Unit Sales Price. Gross line revenue before coupons and tax.",
     isa="Sales Measure", syn="Gross Sales", tier="seed", cols=sales("ext_sales_price"))
term("Extended Discount Amount", "Quantity Sold x (Unit List Price - Unit Sales Price). Markdown from list.",
     isa="Sales Measure", syn="Markdown", cols=sales("ext_discount_amt"))
term("Coupon Amount", "Coupon discount applied to the line, after the sales price.", isa="Sales Measure", cols=sales("coupon_amt"))
term("Net Paid", "Extended Sales Price - Coupon Amount. Revenue the customer actually paid, before tax and shipping.",
     isa="Sales Measure", syn="Net Sales|Net Revenue", tier="seed", cols=sales("net_paid"))
term("Extended Tax", "Sales tax charged on the line, computed on Net Paid.", isa="Sales Measure", cols=sales("ext_tax"))
term("Net Paid Including Tax", "Net Paid + Extended Tax.", isa="Sales Measure", cols=sales("net_paid_inc_tax"))
term("Extended Ship Cost", "Shipping charge on a catalog or web line.", isa="Sales Measure",
     cols=["catalog_sales.cs_ext_ship_cost","web_sales.ws_ext_ship_cost"])
term("Net Paid Including Ship", "Net Paid + Extended Ship Cost (catalog and web only).", isa="Sales Measure",
     cols=["catalog_sales.cs_net_paid_inc_ship","web_sales.ws_net_paid_inc_ship"])
term("Net Paid Including Ship and Tax", "Net Paid + Extended Ship Cost + Extended Tax (catalog and web only).",
     isa="Sales Measure", cols=["catalog_sales.cs_net_paid_inc_ship_tax","web_sales.ws_net_paid_inc_ship_tax"])
term("Net Profit", "Net Paid - Extended Wholesale Cost. Line-level gross profit.", isa="Sales Measure",
     syn="Gross Profit", tier="seed", cols=sales("net_profit"))

# ---------------------------------------------------------------- return measures
term("Return Quantity", "Units returned on the line.", isa="Return Measure", tier="seed", cols=rets("return_quantity"))
term("Return Amount", "Value of the returned goods refunded to the customer, before tax.", isa="Return Measure", tier="seed",
     cols=["store_returns.sr_return_amt","catalog_returns.cr_return_amount","web_returns.wr_return_amt"])
term("Return Tax", "Tax refunded on the return.", isa="Return Measure", cols=rets("return_tax"))
term("Return Amount Including Tax", "Return Amount + Return Tax.", isa="Return Measure", cols=rets("return_amt_inc_tax"))
term("Return Fee", "Restocking or handling fee charged to the customer for the return.", isa="Return Measure", cols=rets("fee"))
term("Return Ship Cost", "Shipping cost incurred to return the goods.", isa="Return Measure", cols=rets("return_ship_cost"))
term("Refunded Cash", "Portion of the refund paid in cash.", isa="Return Measure", see="Reversed Charge|Store Credit",
     cols=rets("refunded_cash"))
term("Reversed Charge", "Portion of the refund returned by reversing the original card charge.", isa="Return Measure",
     cols=rets("reversed_charge"))
term("Store Credit", "Portion of the refund issued as store or account credit.", isa="Return Measure",
     cols=["store_returns.sr_store_credit","catalog_returns.cr_store_credit","web_returns.wr_account_credit"])
term("Net Loss", "Total loss to the retailer from the return. Verify exact formula against dsdgen pricing.c before governing.",
     "Derived by dsdgen from return amount including tax, fee and shipping cost, net of the refund components. Treat as a measure, not a definition, until verified.",
     isa="Return Measure", cols=rets("net_loss"))

# ---------------------------------------------------------------- inventory
term("Inventory", "Weekly snapshot of units on hand per item per warehouse.",
     "Semi-additive: sum across items and warehouses, never across dates.", isa="Dimension", tier="seed",
     cols=["inventory.inv_date_sk"])
term("Quantity On Hand", "Units of the item held at the warehouse on the snapshot date.", tier="seed",
     cols=["inventory.inv_quantity_on_hand"])

# ---------------------------------------------------------------- derived KPIs (no columns; definitions only)
term("Total Net Sales", "SUM(Net Paid) across the selected channel(s).", isa="Sales Measure", syn="Revenue", tier="seed")
term("Gross Margin", "SUM(Net Profit) / SUM(Net Paid). Profit as a share of net revenue.", isa="Sales Measure", tier="seed")
term("Gross Margin Amount", "SUM(Net Profit).", isa="Sales Measure")
term("Average Ticket", "SUM(Net Paid) / COUNT(DISTINCT Ticket Number or Order Number).", isa="Sales Measure")
term("Units Per Transaction", "SUM(Quantity Sold) / COUNT(DISTINCT Ticket Number or Order Number).", isa="Sales Measure")
term("Discount Rate", "SUM(Extended Discount Amount + Coupon Amount) / SUM(Extended List Price).", isa="Sales Measure")
term("Return Rate", "SUM(Return Amount) / SUM(Net Paid) for the same channel and period.", isa="Return Measure", tier="seed")
term("Return Quantity Rate", "SUM(Return Quantity) / SUM(Quantity Sold).", isa="Return Measure")
term("Net Sales After Returns", "SUM(Net Paid) - SUM(Return Amount).", isa="Sales Measure", syn="Net Net Sales")
term("Promotion Lift", "Net Paid on lines with a promotion vs lines without, for the same item and period.", isa="Sales Measure")
term("Active Customers", "COUNT(DISTINCT Customer) with at least one sale in the period.")
term("Weeks of Supply", "Quantity On Hand / average weekly Quantity Sold for the item.")

# ---------------------------------------------------------------- write outputs
HDR = ["GlossaryName","TermName","ShortDescription","LongDescription","Examples","Abbreviation","Usage",
       "AdditionalAttributes","TranslationTerms","ValidValuesFor","Synonyms","ReplacedBy","ValidValues",
       "ReplacementTerms","SeeAlso","TranslatedTerms","IsA","Antonyms","Classifies","PreferredToTerms","PreferredTerms"]
names = {t["name"] for t in T}
def ref(field):
    out = []
    for n in field.split("|") if field else []:
        assert n in names, f"unknown related term: {n}"
        out.append(f"{GLOSSARY}:{n}")
    return "|".join(out)

def write(path, terms):
    with open(path, "w", newline="") as f:
        w = csv.writer(f); w.writerow(HDR)
        keep = {t["name"] for t in terms}
        for t in terms:
            # only keep relationships whose target is also in this file
            def keepref(field): return "|".join(x for x in ref(field).split("|") if x and x.split(":",1)[1] in keep)
            long = t["long"] + (f" Also known as: {t['syn'].replace('|', ', ')}." if t["syn"] else "")
            w.writerow([GLOSSARY, t["name"], t["short"], long.strip(), t["examples"], t["abbr"], "",
                        "", "", "", "", "", "", "", keepref(t["see"]), "", keepref(t["isa"]), "", "", "", ""])

write(os.path.join(OUT, "tpcds_glossary_full.csv"), T)
write(os.path.join(OUT, "tpcds_glossary_seed.csv"), [t for t in T if t["tier"] == "seed"])

with open(os.path.join(OUT, "tpcds_term_columns.csv"), "w", newline="") as f:
    w = csv.writer(f); w.writerow(["term","table","column","tier"])
    for t in T:
        for c in t["cols"]:
            tb, col = c.split(".")
            w.writerow([t["name"], tb, col, t["tier"]])

print(f"{len(T)} terms ({sum(t['tier']=='seed' for t in T)} seed), "
      f"{sum(len(t['cols']) for t in T)} column assignments")
