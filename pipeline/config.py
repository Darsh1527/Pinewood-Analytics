"""Central configuration for the Pinewood analytics pipeline."""
from pathlib import Path

# ---------------------------------------------------------------- paths
PROJECT_ROOT = Path(__file__).resolve().parent.parent
RAW_DATA_DIR = PROJECT_ROOT / "data" / "raw"
DB_PATH = PROJECT_ROOT / "data" / "pinewood.duckdb"
LOG_DIR = PROJECT_ROOT / "logs"
EXPORT_DIR = PROJECT_ROOT / "powerbi" / "data"
SQL_DIR = PROJECT_ROOT / "sql"
VALIDATION_DIR = PROJECT_ROOT / "validation"

# ---------------------------------------------------------------- sources
# file name pattern: {source}_{table}_{YYYY}_{MM}.csv
SOURCE_TABLES = {
    "pcc_residents": "pointclickcare",
    "pcc_incidents": "pointclickcare",
    "pcc_care_history": "pointclickcare",
    "yardi_units": "yardi",
    "yardi_leases": "yardi",
    "adp_shifts": "adp",
    "gbp_reviews": "google_business_profile",
    "hubspot_leads": "hubspot",
}

# ---------------------------------------------------------------- business rules
VALID_COMMUNITIES = [f"C{i:03d}" for i in range(1, 15)]

# ASSUMPTION: no community master file is provided. The assignment defines
# regions by state (OR / AZ / TX). We assign communities to states in ID
# order; this is documented in the README and trivially replaceable once
# the client provides the real mapping.
COMMUNITY_MASTER = {
    # community_id: (name, city, state, region)
    "C001": ("Pinewood Willamette", "Portland", "OR", "Pacific Northwest"),
    "C002": ("Pinewood Cascade", "Bend", "OR", "Pacific Northwest"),
    "C003": ("Pinewood Rose City", "Portland", "OR", "Pacific Northwest"),
    "C004": ("Pinewood Umpqua", "Roseburg", "OR", "Pacific Northwest"),
    "C005": ("Pinewood Coast", "Newport", "OR", "Pacific Northwest"),
    "C006": ("Pinewood Desert Sun", "Phoenix", "AZ", "Southwest"),
    "C007": ("Pinewood Saguaro", "Tucson", "AZ", "Southwest"),
    "C008": ("Pinewood Red Rock", "Sedona", "AZ", "Southwest"),
    "C009": ("Pinewood Camelback", "Scottsdale", "AZ", "Southwest"),
    "C010": ("Pinewood Sonoran", "Mesa", "AZ", "Southwest"),
    "C011": ("Pinewood Hill Country", "Austin", "TX", "South"),
    "C012": ("Pinewood Lone Star", "Dallas", "TX", "South"),
    "C013": ("Pinewood Gulf Breeze", "Houston", "TX", "South"),
    "C014": ("Pinewood Alamo", "San Antonio", "TX", "South"),
}

# canonical care levels and every variant observed in the source data
CARE_LEVEL_MAP = {
    "IL": "IL", "INDEPENDENT": "IL", "INDEPENDENT LIVING": "IL",
    "AL": "AL", "ASSISTED": "AL", "ASSISTED LIVING": "AL",
    "MC": "MC", "MEMORY": "MC", "MEMORY CARE": "MC",
}

ACUITY_MIN, ACUITY_MAX = 1, 10
SEVERITY_MIN, SEVERITY_MAX = 1, 5
RATING_MIN, RATING_MAX = 1, 5
MAX_SHIFT_HOURS = 16

# analysis window covered by the extracts
WINDOW_START = "2025-01-01"
WINDOW_END = "2025-06-30"
