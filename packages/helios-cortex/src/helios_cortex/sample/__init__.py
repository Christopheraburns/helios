"""A sample project to try Helios on: ``helios init --sample`` (burndown 9.1).

The TPC-DS store-sales star (``store_sales`` and the six tables it joins to: dates,
items, stores, customer and household demographics, income bands) in a DuckDB file, and
the semantic model for it, published. The model is the one Helios proposed from a real
TPC-DS warehouse and a person reviewed, trimmed to the star; the rows are generated
here, the same every time (a fixed seed), with TPC-DS's own arithmetic (net paid is the
extended sales price less coupons, profit is net paid less wholesale cost, tax is the
store's rate) so the metrics mean what they say.

``create(config)`` writes a starter ``helios.toml`` there with a project-local
``data_dir``, builds the warehouse its default connection names, and registers and
publishes the model, as ``helios source add``, ``helios model add`` and ``helios publish``
would have.
"""
from __future__ import annotations

import csv
import json
import random
import tempfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from importlib.resources import files
from pathlib import Path
from typing import Any

MODEL_ID = "tpcds"
DATA_SOURCE_ID = "warehouse"
SEED = 9001
STORE_SALES_ROWS = 60_000
FIRST_DAY, LAST_DAY = date(1998, 1, 1), date(2002, 12, 31)
FIRST_DATE_SK = 2450815                     # TPC-DS's surrogate key for 1998-01-01

TYPES = {"string": "VARCHAR", "bigint": "BIGINT", "int": "INTEGER", "date": "DATE"}


class SampleError(Exception):
    """The sample cannot be created where it was asked for."""


@dataclass(frozen=True)
class Sample:
    config: Path
    warehouse: Path
    tables: dict[str, int]                  # rows per table
    model_id: str
    revision_id: str


def model_document() -> dict[str, Any]:
    """The sample's semantic model (the TPC-DS store-sales star), as an Ossie document."""
    import yaml

    return yaml.safe_load(files(__package__).joinpath("tpcds_star.ossie.yaml").read_text())


def table_columns() -> dict[str, list[tuple[str, str]]]:
    """Each sample table's columns and their TPC-DS types, as in the warehouse it came from."""
    spec = json.loads(files(__package__).joinpath("tpcds_star.tables.json").read_text())
    return {table: [(name, kind) for name, kind in columns] for table, columns in spec["tables"].items()}


def create(config: Path, *, force: bool = False) -> Sample:
    """Write ``config`` (a starter helios.toml for the sample), build the DuckDB warehouse
    its default connection names, and register and publish the TPC-DS model. Refuses to
    replace an existing config or warehouse unless ``force``."""
    from helios_cortex.commands import STARTER_CONFIG
    from helios_cortex.settings import Settings

    config = Path(config)
    for existing in (config, config.parent / "warehouse.duckdb"):   # the warehouse the starter file names
        if existing.exists() and not force:
            raise SampleError(f"{existing} already exists; pass --force to replace it")
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(SAMPLE_HEADER + STARTER_CONFIG.replace('# data_dir = "helios-data"', 'data_dir = "helios-data"'))
    settings = Settings.from_file(config)
    connection = settings.connection("default")
    if connection is None or connection.engine != "duckdb" or not connection.options.get("path"):
        raise SampleError("the sample needs [connections.default] to be a DuckDB file (check HELIOS_* variables)")
    warehouse = Path(connection.options["path"])
    if warehouse.exists():
        if not force:
            raise SampleError(f"{warehouse} already exists; pass --force to replace it")
        warehouse.unlink()
    tables = build_warehouse(warehouse)
    revision_id = publish(settings)
    return Sample(config, warehouse, tables, MODEL_ID, revision_id)


SAMPLE_HEADER = """\
# A sample project (helios init --sample): the TPC-DS store-sales star in
# warehouse.duckdb, and its semantic model, published as "tpcds", in helios-data/.

"""


def publish(settings: Any) -> str:
    """Register the warehouse and the model, and publish the model; returns the revision id."""
    from helios_cortex.artifacts import ArtifactStore
    from helios_cortex.domain import DataSource, DataSourceReference, Model
    from helios_cortex.ossie import dump_yaml
    from helios_cortex.registry import SemanticRevision
    from helios_cortex.registry.sql import SqlRegistry

    registry = SqlRegistry.from_settings(settings)
    registry.save_data_source(DataSource(DATA_SOURCE_ID, "TPC-DS sample", "duckdb", "default",
                                         description="The TPC-DS store-sales star (helios init --sample)."))
    registry.save_model(Model(MODEL_ID, "TPC-DS sample", (DataSourceReference(DATA_SOURCE_ID),),
                              description="The TPC-DS store-sales star."), status="published")
    document = model_document()
    published_at = datetime.now(timezone.utc)
    manifest: dict[str, Any] = {
        "model_id": MODEL_ID, "run_id": None, "published_at": published_at.isoformat(), "published_by": None,
        "datasets": len(document["datasets"]), "relationships": len(document["relationships"]),
        "metrics": len(document["metrics"]), "source": "helios init --sample",
    }
    ArtifactStore.from_settings(settings).write_published_ossie(MODEL_ID, document, dump_yaml(document), manifest)
    registry.save_semantic_revision(SemanticRevision(
        id=manifest["revision_id"], model_id=MODEL_ID, sha256=manifest["sha256"], artifact_path=manifest["path"],
        published_at=published_at, size_bytes=manifest["size_bytes"], ossie_version=manifest.get("ossie_version")))
    return str(manifest["revision_id"])


# ---------------------------------------------------------------------------- the data
def build_warehouse(path: Path) -> dict[str, int]:
    """Create the DuckDB file at ``path`` with schema ``tpcds`` and the sample's rows."""
    import duckdb

    columns = table_columns()
    rows = generate(random.Random(SEED))
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as scratch, duckdb.connect(str(path)) as db:
        db.execute("CREATE SCHEMA tpcds")
        for table, spec in columns.items():
            db.execute(f"CREATE TABLE tpcds.{table} ("
                       + ", ".join(f"{name} {TYPES.get(kind, kind.upper())}" for name, kind in spec) + ")")
            names = [name for name, _ in spec]
            csv_path = Path(scratch) / f"{table}.csv"
            with csv_path.open("w", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(names)
                for row in rows[table]:
                    writer.writerow(["" if row.get(name) is None else row[name] for name in names])
            db.execute(f"COPY tpcds.{table} FROM '{csv_path.as_posix()}' (HEADER, NULLSTR '')")
        return {table: int((db.execute(f"SELECT COUNT(*) FROM tpcds.{table}").fetchone() or (0,))[0])
                for table in columns}


CATEGORIES = {
    "Books": ["fiction", "history", "reference", "cooking"], "Children": ["infants", "toddlers", "school-uniforms"],
    "Electronics": ["audio", "cameras", "televisions", "portable"], "Home": ["furniture", "kids", "lighting", "rugs"],
    "Jewelry": ["rings", "bracelets", "estate"], "Men": ["accessories", "pants", "shirts", "sports-apparel"],
    "Music": ["classical", "country", "pop", "rock"], "Shoes": ["athletic", "mens", "womens"],
    "Sports": ["camping", "fitness", "golf", "outdoor"], "Women": ["dresses", "maternity", "swimwear"],
}
MANUFACTURERS = ["amalg", "edu pack", "export", "import", "brand", "corp", "maxi", "scholar", "univ", "namel"]
COLORS = ["red", "navy", "olive", "smoke", "plum", "ivory", "khaki", "lime", "salmon", "slate"]
SIZES = ["small", "medium", "large", "extra large", "petite", "N/A"]
UNITS = ["Each", "Dozen", "Box", "Case", "Pound", "Ounce"]
STORES = [  # name, city, county, state, zip, tax rate
    ("ought", "Midway", "Williamson County", "TN", "31904", "0.07"),
    ("able", "Fairview", "Williamson County", "TN", "35709", "0.07"),
    ("pri", "Oak Grove", "Ziebach County", "SD", "57758", "0.04"),
    ("ese", "Five Points", "Walker County", "GA", "30753", "0.06"),
    ("anti", "Pleasant Hill", "Bronx County", "NY", "10456", "0.09"),
    ("cally", "Riverside", "Harris County", "TX", "77002", "0.08"),
    ("ation", "Centerville", "Franklin County", "OH", "43215", "0.06"),
    ("eing", "Salem", "Marion County", "OR", "97301", "0.00"),
    ("bar", "Georgetown", "Jefferson County", "AL", "35203", "0.05"),
    ("n st", "Greenwood", "Saline County", "KS", "67401", "0.06"),
]
DAY_NAMES = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]


def _money(value: float) -> str:
    return f"{value:.2f}"


def generate(rnd: random.Random) -> dict[str, list[dict[str, Any]]]:
    """Every table's rows, from ``rnd`` (seeded, so the same rows every time)."""
    tables: dict[str, list[dict[str, Any]]] = {}

    days: list[dict[str, Any]] = []
    day = FIRST_DAY
    while day <= LAST_DAY:
        sk = FIRST_DATE_SK + (day - FIRST_DAY).days
        quarter = (day.month - 1) // 3 + 1
        dow = (day.weekday() + 1) % 7                    # TPC-DS: Sunday is 0
        month_start = day.replace(day=1)
        next_month = (month_start + timedelta(days=32)).replace(day=1)
        days.append({
            "d_date_sk": sk, "d_date_id": f"D{sk}", "d_date": day.isoformat(),
            "d_month_seq": (day.year - 1900) * 12 + day.month - 1, "d_week_seq": (sk - FIRST_DATE_SK) // 7 + 5114,
            "d_quarter_seq": (day.year - 1900) * 4 + quarter - 1, "d_year": day.year, "d_dow": dow,
            "d_moy": day.month, "d_dom": day.day, "d_qoy": quarter, "d_fy_year": day.year,
            "d_fy_quarter_seq": (day.year - 1900) * 4 + quarter - 1, "d_fy_week_seq": (sk - FIRST_DATE_SK) // 7 + 5114,
            "d_day_name": DAY_NAMES[dow], "d_quarter_name": f"{day.year}Q{quarter}",
            "d_holiday": "Y" if (day.month, day.day) in {(1, 1), (7, 4), (12, 25)} else "N",
            "d_weekend": "Y" if dow in (0, 6) else "N",
            "d_following_holiday": "Y" if (day.month, day.day) in {(1, 2), (7, 5), (12, 26)} else "N",
            "d_first_dom": FIRST_DATE_SK + (month_start - FIRST_DAY).days,
            "d_last_dom": FIRST_DATE_SK + (next_month - timedelta(days=1) - FIRST_DAY).days,
            "d_same_day_ly": sk - 365, "d_same_day_lq": sk - 91,
            "d_current_day": "N", "d_current_week": "N", "d_current_month": "N", "d_current_quarter": "N",
            "d_current_year": "N",
        })
        day += timedelta(days=1)
    tables["date_dim"] = days

    items: list[dict[str, Any]] = []
    category_names = sorted(CATEGORIES)
    for sk in range(1, 301):
        category = rnd.choice(category_names)
        klass = rnd.choice(CATEGORIES[category])
        manufact_id = rnd.randint(1, 100)
        brand_id = rnd.randint(1, 10)
        maker = MANUFACTURERS[manufact_id % len(MANUFACTURERS)]
        price = round(rnd.uniform(0.99, 99.99), 2)
        items.append({
            "i_item_sk": sk, "i_item_id": f"I{sk:08d}", "i_rec_start_date": "1997-10-27", "i_rec_end_date": None,
            "i_item_desc": f"{rnd.choice(COLORS).title()} {klass.replace('-', ' ')} from {maker}",
            "i_current_price": _money(price), "i_wholesale_cost": _money(price * rnd.uniform(0.3, 0.7)),
            "i_brand_id": category_names.index(category) * 1000 + brand_id,
            "i_brand": f"{maker}{category.lower()} #{brand_id}",
            "i_class_id": CATEGORIES[category].index(klass) + 1, "i_class": klass,
            "i_category_id": category_names.index(category) + 1, "i_category": category,
            "i_manufact_id": manufact_id, "i_manufact": maker, "i_size": rnd.choice(SIZES),
            "i_formulation": f"{rnd.randrange(10**8):08d}", "i_color": rnd.choice(COLORS), "i_units": rnd.choice(UNITS),
            "i_container": "Unknown", "i_manager_id": rnd.randint(1, 100), "i_product_name": f"{maker} {klass} {sk}",
        })
    tables["item"] = items

    stores: list[dict[str, Any]] = []
    for sk, (name, city, county, state, zip_code, tax) in enumerate(STORES, start=1):
        stores.append({
            "s_store_sk": sk, "s_store_id": f"S{sk:08d}", "s_rec_start_date": "1997-03-13", "s_rec_end_date": None,
            "s_closed_date_sk": None, "s_store_name": name, "s_number_employees": rnd.randint(200, 300),
            "s_floor_space": rnd.randint(5_000_000, 10_000_000), "s_hours": rnd.choice(["8AM-4PM", "8AM-8AM", "8AM-12AM"]),
            "s_manager": f"Manager {sk}", "s_market_id": rnd.randint(1, 10), "s_geography_class": "Unknown",
            "s_market_desc": f"The {city} market", "s_market_manager": f"Market manager {sk}",
            "s_division_id": 1, "s_division_name": "Unknown", "s_company_id": 1, "s_company_name": "Unknown",
            "s_street_number": str(rnd.randint(1, 999)), "s_street_name": rnd.choice(["Main", "Oak", "Park", "Elm"]),
            "s_street_type": rnd.choice(["Street", "Avenue", "Road", "Lane"]), "s_suite_number": f"Suite {rnd.randint(1, 500)}",
            "s_city": city, "s_county": county, "s_state": state, "s_zip": zip_code, "s_country": "United States",
            "s_gmt_offset": "-5.00" if state not in ("SD", "TX", "KS", "AL", "OR") else "-6.00",
            "s_tax_percentage": tax,
        })
    tables["store"] = stores

    tables["income_band"] = [{"ib_income_band_sk": k, "ib_lower_bound": (k - 1) * 10_000 + (1 if k > 1 else 0),
                              "ib_upper_bound": k * 10_000} for k in range(1, 21)]

    households: list[dict[str, Any]] = []
    potentials = ["0-500", "501-1000", "1001-5000", "5001-10000", ">10000", "Unknown"]
    for band in range(1, 21):
        for potential in potentials:
            for vehicles in range(0, 5):
                households.append({"hd_demo_sk": len(households) + 1, "hd_income_band_sk": band,
                                   "hd_buy_potential": potential, "hd_dep_count": rnd.randint(0, 9),
                                   "hd_vehicle_count": vehicles})
    tables["household_demographics"] = households

    customers: list[dict[str, Any]] = []
    educations = ["Primary", "Secondary", "College", "2 yr Degree", "4 yr Degree", "Advanced Degree", "Unknown"]
    for gender in ("M", "F"):
        for marital in ("M", "S", "D", "W", "U"):
            for education in educations:
                for rating in ("Low Risk", "Good", "High Risk", "Unknown"):
                    customers.append({"cd_demo_sk": len(customers) + 1, "cd_gender": gender,
                                      "cd_marital_status": marital, "cd_education_status": education,
                                      "cd_purchase_estimate": rnd.randrange(500, 10_001, 500),
                                      "cd_credit_rating": rating, "cd_dep_count": rnd.randint(0, 6),
                                      "cd_dep_employed_count": rnd.randint(0, 6), "cd_dep_college_count": rnd.randint(0, 6)})
    tables["customer_demographics"] = customers

    # Sales: more each year, a holiday peak, baskets of one to eight lines on one ticket.
    weights = [(1 + 0.08 * (d["d_year"] - 1998)) * (1.8 if d["d_moy"] == 12 else 1.4 if d["d_moy"] == 11 else 1.0)
               for d in days]
    sales: list[dict[str, Any]] = []
    ticket = 0
    while len(sales) < STORE_SALES_ROWS:
        ticket += 1
        sold = rnd.choices(days, weights)[0]["d_date_sk"]
        store = rnd.choice(stores)
        customer, cdemo, hdemo = rnd.randint(1, 20_000), rnd.randint(1, len(customers)), rnd.randint(1, len(households))
        address, sold_time = rnd.randint(1, 10_000), rnd.randint(28_800, 75_600)
        promo = rnd.randint(1, 30) if rnd.random() < 0.4 else None
        for _ in range(min(rnd.randint(1, 8), STORE_SALES_ROWS - len(sales))):
            item = rnd.choice(items)
            quantity = rnd.randint(1, 100)
            wholesale = float(item["i_wholesale_cost"])
            list_price = round(wholesale * rnd.uniform(1.2, 2.0), 2)
            sales_price = round(list_price * rnd.uniform(0.4, 1.0), 2)
            ext_sales, ext_list, ext_wholesale = (round(quantity * v, 2) for v in (sales_price, list_price, wholesale))
            coupon = round(ext_sales * rnd.uniform(0.05, 0.3), 2) if rnd.random() < 0.15 else 0.0
            net_paid = round(ext_sales - coupon, 2)
            sales_tax = round(net_paid * float(store["s_tax_percentage"]), 2)
            sales.append({
                "ss_sold_date_sk": sold, "ss_sold_time_sk": sold_time, "ss_item_sk": item["i_item_sk"],
                "ss_customer_sk": customer, "ss_cdemo_sk": cdemo, "ss_hdemo_sk": hdemo, "ss_addr_sk": address,
                "ss_store_sk": store["s_store_sk"], "ss_promo_sk": promo, "ss_ticket_number": ticket,
                "ss_quantity": quantity, "ss_wholesale_cost": _money(wholesale), "ss_list_price": _money(list_price),
                "ss_sales_price": _money(sales_price), "ss_ext_discount_amt": _money(ext_list - ext_sales),
                "ss_ext_sales_price": _money(ext_sales), "ss_ext_wholesale_cost": _money(ext_wholesale),
                "ss_ext_list_price": _money(ext_list), "ss_ext_tax": _money(sales_tax), "ss_coupon_amt": _money(coupon),
                "ss_net_paid": _money(net_paid), "ss_net_paid_inc_tax": _money(net_paid + sales_tax),
                "ss_net_profit": _money(net_paid - ext_wholesale),
            })
    tables["store_sales"] = sales
    return tables
