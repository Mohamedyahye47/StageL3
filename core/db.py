# ============================================================
#  core/db.py
# ============================================================

import sqlite3
import os
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "databridge.db")

# ============================================================
#  SCHEMA
# ============================================================

SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL,
    code        TEXT    UNIQUE NOT NULL,
    base_url    TEXT,
    description TEXT,
    created_at  TEXT    DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS topics (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL,
    source_id   INTEGER NOT NULL,
    description TEXT,
    FOREIGN KEY (source_id) REFERENCES sources(id)
);

CREATE TABLE IF NOT EXISTS indicators (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    code      TEXT    NOT NULL,
    label     TEXT    NOT NULL,
    source_id INTEGER NOT NULL,
    topic_id  INTEGER,
    unit      TEXT,
    FOREIGN KEY (source_id) REFERENCES sources(id),
    FOREIGN KEY (topic_id)  REFERENCES topics(id)
);

CREATE TABLE IF NOT EXISTS datasets (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    name           TEXT    UNIQUE NOT NULL,
    description    TEXT,
    source_id      INTEGER NOT NULL,
    format         TEXT    CHECK(format    IN ('long', 'wide')),
    frequency      TEXT    CHECK(frequency IN ('annual', 'monthly', 'quarterly', 'daily')),
    ods_dataset_id TEXT,
    status         TEXT    DEFAULT 'active',
    created_at     TEXT    DEFAULT (datetime('now')),
    updated_at     TEXT    DEFAULT (datetime('now')),
    FOREIGN KEY (source_id) REFERENCES sources(id)
);

CREATE TABLE IF NOT EXISTS dataset_indicators (
    dataset_id INTEGER NOT NULL,
    indicator_id INTEGER NOT NULL,
    PRIMARY KEY (dataset_id, indicator_id),
    FOREIGN KEY (dataset_id) REFERENCES datasets(id),
    FOREIGN KEY (indicator_id) REFERENCES indicators(id)
);

CREATE TABLE IF NOT EXISTS push_logs (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    pushed_at      TEXT    DEFAULT (datetime('now')),
    dataset_name   TEXT    NOT NULL,
    source_code    TEXT    NOT NULL,
    ods_dataset_id TEXT    NOT NULL,
    row_count      INTEGER NOT NULL,
    status         TEXT    CHECK(status IN ('success', 'error')),
    error_message  TEXT
);
"""

# ============================================================
#  MASTER METADATA (Centralized)
# ============================================================

MASTER_METADATA = {
    "IMF": {
        "source": {"name": "International Monetary Fund", "base_url": "https://www.imf.org/external/datamapper/api/v1", "description": "IMF — World Economic Outlook (WEO) Database"},
        "topics": {
            "macro": {"name": "Macro Aggregates", "description": "National accounts, GDP and growth indicators"},
            "inflation": {"name": "Inflation & Prices", "description": "Consumer price indices and inflation rates"},
            "fiscal": {"name": "Public Finance", "description": "Government revenue, expenditure, debt and balances"},
            "external": {"name": "External Sector", "description": "Current account, imports and exports"},
            "labour": {"name": "Labour Market", "description": "Employment, unemployment and population"},
        },
        "datasets": {
            "mauritania_weo_macro_long": {
                "description": "Agrégats macroéconomiques WEO — Mauritanie", "format": "long", "frequency": "annual", "ods_dataset_id": "mauritania-weo-macro", "topic_key": "macro",
                "indicators": {
                    "NGDP_RPCH": {"label": "Real GDP growth (annual %)", "unit": "%"},
                    "NGDP_RPCHMK": {"label": "Real GDP per capita growth (annual %)", "unit": "%"},
                    "NGDPDPC": {"label": "GDP per capita, current USD", "unit": "USD"},
                    "NGDPD": {"label": "GDP, current prices (USD billions)", "unit": "USD bn"},
                    "PPPGDP": {"label": "GDP, PPP (international dollar billions)", "unit": "Intl$ bn"},
                    "PPPPC": {"label": "GDP per capita, PPP (international dollars)", "unit": "Intl$"},
                }
            },
            "mauritania_weo_inflation_long": {
                "description": "Inflation et prix WEO — Mauritanie", "format": "long", "frequency": "annual", "ods_dataset_id": "mauritania-weo-inflation", "topic_key": "inflation",
                "indicators": {
                    "PCPIPCH": {"label": "Inflation, average consumer prices (annual %)", "unit": "%"},
                    "PCPIEPCH": {"label": "Inflation, end of period consumer prices (%)", "unit": "%"},
                }
            },
            "mauritania_weo_fiscal_wide": {
                "description": "Finances publiques WEO — Mauritanie", "format": "wide", "frequency": "annual", "ods_dataset_id": "mauritania-weo-fiscal", "topic_key": "fiscal",
                "indicators": {
                    "GGR_G01_GDP_PT": {"label": "Revenue, all taxes (% of GDP)", "unit": "%"},
                    "GGX_GDP_PT": {"label": "Expenditure (% of GDP)", "unit": "%"},
                    "GGXCNL_GDP_PT": {"label": "Net lending/borrowing (% of GDP)", "unit": "%"},
                    "GGXWDG_GDP_PT": {"label": "General government gross debt (% of GDP)", "unit": "%"},
                }
            },
            "mauritania_weo_external_long": {
                "description": "Secteur extérieur WEO — Mauritanie", "format": "long", "frequency": "annual", "ods_dataset_id": "mauritania-weo-external", "topic_key": "external",
                "indicators": {
                    "BCA_NGDPD": {"label": "Current account balance (% of GDP)", "unit": "%"},
                    "BCA": {"label": "Current account balance (USD billions)", "unit": "USD bn"},
                }
            },
            "mauritania_weo_labour_long": {
                "description": "Population et emploi WEO — Mauritanie", "format": "long", "frequency": "annual", "ods_dataset_id": "mauritania-weo-labour", "topic_key": "labour",
                "indicators": {
                    "LP": {"label": "Population (millions)", "unit": "millions"},
                    "LUR": {"label": "Unemployment rate (%)", "unit": "%"},
                }
            }
        }
    },
    "WB": {
        "source": {"name": "World Bank", "base_url": "https://api.worldbank.org/v2", "description": "World Bank — World Development Indicators (WDI)"},
        "topics": {
            "human_development": {"name": "Human Development", "description": "Education, health and poverty indicators"},
            "investment": {"name": "Investment & Capital Formation", "description": "Gross capital formation and fixed investment indicators"},
            "external_trade": {"name": "External Trade", "description": "Exports, imports and external balance"},
        },
        "datasets": {
            "mauritania_human_development_long": {
                "description": "Indicateurs de développement humain — Mauritanie", "format": "long", "frequency": "annual", "ods_dataset_id": "mauritania-human-development-indicators", "topic_key": "human_development",
                "indicators": {
                    "SP.DYN.LE00.IN": {"label": "Life expectancy at birth, total (years)", "unit": "years"},
                    "SH.DYN.NMRT": {"label": "Mortality rate, neonatal (per 1,000 live births)", "unit": "per 1,000"},
                    "SH.DYN.MORT": {"label": "Mortality rate, under-5 (per 1,000 live births)", "unit": "per 1,000"},
                    "SH.XPD.CHEX.GD.ZS": {"label": "Current health expenditure (% of GDP)", "unit": "%"},
                    "SE.PRM.ENRR": {"label": "School enrollment, primary (% gross)", "unit": "%"},
                    "SE.SEC.NENR": {"label": "School enrollment, secondary (% net)", "unit": "%"},
                    "SE.ADT.LITR.ZS": {"label": "Literacy rate, adult total (% of people ages 15+)", "unit": "%"},
                    "SE.XPD.TOTL.GD.ZS": {"label": "Government expenditure on education, total (% of GDP)", "unit": "%"},
                    "NY.GDP.PCAP.CD": {"label": "GDP per capita (current US$)", "unit": "USD"},
                    "SI.POV.DDAY": {"label": "Poverty headcount ratio at $2.15/day 2017 PPP (% of population)", "unit": "%"},
                    "SH.H2O.SMDW.ZS": {"label": "People using safely managed drinking water (% of population)", "unit": "%"},
                }
            },
            "mauritania_investment_wide": {
                "description": "Investissement et capital — Mauritanie", "format": "wide", "frequency": "annual", "ods_dataset_id": "mauritania-investment-indicators", "topic_key": "investment",
                "indicators": {
                    "NE.GDI.FTOT.ZS": {"label": "Gross fixed capital formation (% of GDP)", "unit": "%"},
                    "NE.GDI.TOTL.ZS": {"label": "Gross capital formation (% of GDP)", "unit": "%"},
                    "BX.KLT.DINV.WD.GD.ZS": {"label": "Foreign direct investment, net inflows (% of GDP)", "unit": "%"},
                }
            },
            "mauritania_trade_wide": {
                "description": "Commerce extérieur — Mauritanie", "format": "wide", "frequency": "annual", "ods_dataset_id": "mauritania-trade-indicators", "topic_key": "external_trade",
                "indicators": {
                    "NE.EXP.GNFS.ZS": {"label": "Exports of goods and services (% of GDP)", "unit": "%"},
                    "NE.IMP.GNFS.ZS": {"label": "Imports of goods and services (% of GDP)", "unit": "%"},
                    "NE.TRD.GNFS.ZS": {"label": "Trade (% of GDP)", "unit": "%"},
                    "BN.CAB.XOKA.GD.ZS": {"label": "Current account balance (% of GDP)", "unit": "%"},
                }
            }
        }
    },
    "YAHOO": {
        "source": {"name": "Yahoo Finance", "base_url": "https://finance.yahoo.com", "description": "Yahoo Finance — commodity futures prices"},
        "topics": {
            "commodities_export": {"name": "Export Commodities", "description": "Gold, iron ore and copper — Mauritania main exports"},
            "commodities_energy": {"name": "Energy", "description": "Crude oil and natural gas prices"},
            "commodities_food": {"name": "Food Imports", "description": "Wheat and corn — Mauritania main food imports"},
        },
        "datasets": {
            "mauritania_commodities_monthly": {
                "description": "Prix des matières premières — Mauritanie (Mensuel)", "format": "wide", "frequency": "monthly", "ods_dataset_id": "mauritania-commodities-monthly", "topic_key": "commodities_export",
                "indicators": {
                    "GC=F": {"label": "gold_price_usd_per_oz", "unit": "USD/oz", "topic_key": "commodities_export"},
                    "HG=F": {"label": "copper_price_usd_per_lb", "unit": "USD/lb", "topic_key": "commodities_export"},
                    "BZ=F": {"label": "brent_crude_price_usd_per_barrel", "unit": "USD/barrel", "topic_key": "commodities_energy"},
                    "CL=F": {"label": "wti_crude_price_usd_per_barrel", "unit": "USD/barrel", "topic_key": "commodities_energy"},
                    "NG=F": {"label": "natural_gas_price_usd_per_mmbtu", "unit": "USD/MMBtu", "topic_key": "commodities_energy"},
                    "ZW=F": {"label": "wheat_price_usd_per_bushel", "unit": "USD/bushel", "topic_key": "commodities_food"},
                    "ZC=F": {"label": "corn_price_usd_per_bushel", "unit": "USD/bushel", "topic_key": "commodities_food"},
                    "62_IRON_ORE": {"label": "iron_ore_price_usd_per_dmtu", "unit": "USD/dmtu", "topic_key": "commodities_export"}
                }
            },
            "mauritania_commodities_annual": {
                "description": "Prix des matières premières — Mauritanie (Annuel)", "format": "wide", "frequency": "annual", "ods_dataset_id": "mauritania-commodities-annual", "topic_key": "commodities_export",
                "indicators": {
                    "GC=F": {"label": "gold_price_usd_per_oz", "unit": "USD/oz", "topic_key": "commodities_export"},
                    "HG=F": {"label": "copper_price_usd_per_lb", "unit": "USD/lb", "topic_key": "commodities_export"},
                    "BZ=F": {"label": "brent_crude_price_usd_per_barrel", "unit": "USD/barrel", "topic_key": "commodities_energy"},
                    "CL=F": {"label": "wti_crude_price_usd_per_barrel", "unit": "USD/barrel", "topic_key": "commodities_energy"},
                    "NG=F": {"label": "natural_gas_price_usd_per_mmbtu", "unit": "USD/MMBtu", "topic_key": "commodities_energy"},
                    "ZW=F": {"label": "wheat_price_usd_per_bushel", "unit": "USD/bushel", "topic_key": "commodities_food"},
                    "ZC=F": {"label": "corn_price_usd_per_bushel", "unit": "USD/bushel", "topic_key": "commodities_food"},
                    "62_IRON_ORE": {"label": "iron_ore_price_usd_per_dmtu", "unit": "USD/dmtu", "topic_key": "commodities_export"}
                }
            }
        }
    }
}

# ============================================================
#  DATABASE HELPERS
# ============================================================

def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_connection() as conn:
        conn.executescript(SCHEMA)

def register_source(code, name, base_url=None, description=None):
    with get_connection() as conn:
        conn.execute("""
            INSERT INTO sources (code, name, base_url, description)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(code) DO UPDATE SET
                name=excluded.name, base_url=excluded.base_url, description=excluded.description
        """, (code, name, base_url, description))
        return conn.execute("SELECT id FROM sources WHERE code = ?", (code,)).fetchone()["id"]

def register_topic(name, source_id, description=None):
    with get_connection() as conn:
        row = conn.execute("SELECT id FROM topics WHERE name = ? AND source_id = ?", (name, source_id)).fetchone()
        if row: return row["id"]
        cursor = conn.execute("INSERT INTO topics (name, source_id, description) VALUES (?, ?, ?)", (name, source_id, description))
        return cursor.lastrowid

def register_indicator(code, label, source_id, topic_id=None, unit=None):
    with get_connection() as conn:
        row = conn.execute("SELECT id FROM indicators WHERE code = ? AND source_id = ?", (code, source_id)).fetchone()
        if row:
            conn.execute("UPDATE indicators SET label=?, topic_id=?, unit=? WHERE id=?", (label, topic_id, unit, row["id"]))
            return row["id"]
        cursor = conn.execute("INSERT INTO indicators (code, label, source_id, topic_id, unit) VALUES (?, ?, ?, ?, ?)", (code, label, source_id, topic_id, unit))
        return cursor.lastrowid

def register_dataset(name, description, source_id, fmt, frequency, ods_dataset_id=None):
    with get_connection() as conn:
        conn.execute("""
            INSERT INTO datasets (name, description, source_id, format, frequency, ods_dataset_id, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(name) DO UPDATE SET
                description=excluded.description, format=excluded.format, 
                frequency=excluded.frequency, ods_dataset_id=excluded.ods_dataset_id, 
                updated_at=excluded.updated_at
        """, (name, description, source_id, fmt, frequency, ods_dataset_id))
        return conn.execute("SELECT id FROM datasets WHERE name = ?", (name,)).fetchone()["id"]

def register_dataset_indicator(dataset_id: int, indicator_id: int):
    with get_connection() as conn:
        conn.execute("INSERT OR IGNORE INTO dataset_indicators (dataset_id, indicator_id) VALUES (?, ?)", (dataset_id, indicator_id))

def log_push(dataset_name, source_code, ods_dataset_id, row_count, status, error_message=None):
    with get_connection() as conn:
        conn.execute("INSERT INTO push_logs (dataset_name, source_code, ods_dataset_id, row_count, status, error_message) VALUES (?, ?, ?, ?, ?, ?)", 
                     (dataset_name, source_code, ods_dataset_id, row_count, status, error_message))

# ============================================================
#  METADATA SEEDING & CONFIG RETRIEVAL
# ============================================================

def seed_metadata():
    print("  [DB] Seeding master metadata...")
    for source_code, meta in MASTER_METADATA.items():
        src_id = register_source(source_code, meta["source"]["name"], meta["source"].get("base_url"), meta["source"].get("description"))
        
        t_ids = {}
        for k, t in meta.get("topics", {}).items():
            t_ids[k] = register_topic(t["name"], src_id, t.get("description"))

        for ds_name, d_cfg in meta.get("datasets", {}).items():
            ds_id = register_dataset(ds_name, d_cfg.get("description"), src_id, d_cfg["format"], d_cfg["frequency"], d_cfg.get("ods_dataset_id"))
            for ind_code, ind_m in d_cfg.get("indicators", {}).items():
                t_key = ind_m.get("topic_key", d_cfg.get("topic_key"))
                ind_id = register_indicator(ind_code, ind_m["label"], src_id, t_ids.get(t_key), ind_m.get("unit"))
                register_dataset_indicator(ds_id, ind_id)

def get_etl_config(source_code: str) -> dict:
    config = {}
    with get_connection() as conn:
        src = conn.execute("SELECT id FROM sources WHERE code = ?", (source_code,)).fetchone()
        if not src: return config
        datasets = conn.execute("SELECT id, name, format, frequency, ods_dataset_id FROM datasets WHERE source_id = ? AND status = 'active'", (src["id"],)).fetchall()
        for ds in datasets:
            config[ds["name"]] = {"format": ds["format"], "frequency": ds["frequency"], "ods_dataset_id": ds["ods_dataset_id"], "indicators": {}}
            indicators = conn.execute("SELECT i.code, i.label, i.unit FROM indicators i JOIN dataset_indicators di ON i.id = di.indicator_id WHERE di.dataset_id = ?", (ds["id"],)).fetchall()
            for ind in indicators:
                config[ds["name"]]["indicators"][ind["code"]] = {"label": ind["label"], "unit": ind["unit"]}
    return config