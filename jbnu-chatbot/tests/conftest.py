import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from store import repo  # noqa: E402

FACILITY_ID = "jbnu:facility/후생관-푸드코트"
SNAPSHOT_ID = "jbnu:snap/coop/2026-08-10T06:00"
SOURCE_URL = "https://coopjbnu.kr/menu/week_menu.php"


@pytest.fixture()
def conn():
    c = repo.connect(":memory:")
    repo.init_db(c)
    c.execute(
        """INSERT INTO facility (id, name, facility_type, source_url, source_type)
           VALUES (?,?,?,?,?)""",
        (FACILITY_ID, "후생관", "식당", SOURCE_URL, "coop"),
    )
    repo.insert_snapshot(
        c, id=SNAPSHOT_ID, source_key="coop_week_menu", url=SOURCE_URL,
        fetched_at="2026-08-10T06:00:00+09:00", http_status=200,
        content_hash="abc123", content_path="snapshots/coop/20260810.json",
        media_type="json",
    )
    c.commit()
    yield c
    c.close()


@pytest.fixture()
def meta():
    return repo.SourceMeta(
        source_id=SNAPSHOT_ID,
        source_url=SOURCE_URL,
        observed_at="2026-08-10T06:00:00+09:00",
        confidence=0.95,
        extraction_method="json_api",
        tier="T1",
        valid_from="2026-08-10",
    )
