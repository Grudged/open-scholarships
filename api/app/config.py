import os
from pathlib import Path

# Repo layout: <repo>/api/app/config.py -> parents[2] is the repo root. The dataset (the actual
# product) is the version-controlled data/ tree; the API just reads it. Both paths are env-
# overridable so a deploy can point at a dataset checked out elsewhere without code changes.
REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.getenv("OS_DATA_DIR", REPO_ROOT / "data"))
SCHEMA_PATH = Path(os.getenv("OS_SCHEMA_PATH", REPO_ROOT / "schema" / "scholarship.schema.json"))

PORT = int(os.getenv("PORT", "8932"))
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "http://localhost:8932").rstrip("/")
DATASET_VERSION = os.getenv("DATASET_VERSION", "0.1.0")

# CC BY 4.0: consumers may use the data freely but MUST attribute. Surfaced in /meta and on the
# X-License / X-Attribution headers of every response so the obligation travels with the data.
DATA_LICENSE = "CC-BY-4.0"
DATA_LICENSE_URL = "https://creativecommons.org/licenses/by/4.0/"
# ASCII-only: this value is also emitted as an HTTP header (X-Attribution), and header values
# must be latin-1 encodable, so no em-dash here.
ATTRIBUTION = "Open Scholarships by Grudged LLC - https://github.com/Grudged/open-scholarships (CC BY 4.0)"
SOURCE_REPO = "https://github.com/Grudged/open-scholarships"
