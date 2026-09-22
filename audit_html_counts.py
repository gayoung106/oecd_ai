import gzip
import re
from pathlib import Path

import pandas as pd

PATTERN = re.compile(
    r'/en/incidents/(\d{4}-\d{2}-\d{2}-[A-Za-z0-9_-]+)'
)

html_dir = Path("data/raw/search_pages")

rows = []

for path in sorted(html_dir.glob("*.html.gz")):
    with gzip.open(path, "rt", encoding="utf-8") as f:
        html = f.read()

    slugs = set(PATTERN.findall(html))

    name = path.name.replace(".html.gz", "")
    start, end = name.split("__")

    rows.append({
        "start": start,
        "end": end,
        "unique_incident_links": len(slugs),
    })

audit = pd.DataFrame(rows)

log_rows = []
import json

with open("data/logs/range_log.jsonl", encoding="utf-8") as f:
    for line in f:
        if not line.strip():
            continue
        x = json.loads(line)
        if x.get("status") == "success":
            log_rows.append({
                "start": x["start"],
                "end": x["end"],
                "reported_count": x.get("reported_count"),
                "parsed_count": x.get("parsed_count"),
            })

logs = pd.DataFrame(log_rows)

out = logs.merge(audit, on=["start", "end"], how="left")

out["html_minus_parsed"] = (
    out["unique_incident_links"] - out["parsed_count"]
)

print("terminal ranges =", len(out))
print("parsed total =", out["parsed_count"].sum())
print("unique HTML links total =", out["unique_incident_links"].sum())
print(
    "ranges where HTML links != parsed =",
    (out["html_minus_parsed"] != 0).sum()
)

print("\nLargest differences:")
print(
    out.sort_values(
        "html_minus_parsed",
        ascending=False
    ).head(30).to_string(index=False)
)

out.to_csv(
    "output/html_count_audit.csv",
    index=False,
    encoding="utf-8-sig"
)