"""Read the existing account usage API; no local metering or price table."""

from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Mapping

import httpx

USAGE_PATH = "/api/v1/platform/usage"
DEFAULT_BUDGET_USD = 20.0


def snapshot(value: object) -> dict[str, object]:
    """Keep the public usage fields, including incomplete pricing evidence."""
    if not isinstance(value, dict) or not isinstance(value.get("meters"), list):
        raise ValueError("The usage API did not return weekly meters.")
    return {
        "read_at": datetime.now(UTC).isoformat(),
        "week": value.get("week"),
        "meters": [
            {key: meter.get(key) for key in (
                "meter", "used", "limit", "mode", "state", "blocked", "exempt"
            )}
            for meter in value["meters"] if isinstance(meter, dict)
        ],
        "breakdown": value.get("breakdown"),
        "measurement": value.get("measurement"),
        "pricing": value.get("pricing"),
        "account_kind": value.get("account_kind"),
    }


async def read_usage(backend_url: str, bearer: str) -> dict[str, object]:
    """A failed read is a warning, never a reason to cancel or repeat a run."""
    try:
        async with httpx.AsyncClient(base_url=backend_url, timeout=10) as client:
            response = await client.get(USAGE_PATH, headers={"Authorization": bearer})
            response.raise_for_status()
            return {"status": "available", **snapshot(response.json())}
    except (httpx.HTTPError, ValueError) as error:
        return {
            "status": "unavailable",
            "read_at": datetime.now(UTC).isoformat(),
            # Response bodies and exception strings may contain credentials.
            "error_type": type(error).__name__,
        }


def _meters(value: Mapping[str, object]) -> dict[str, float] | None:
    rows = value.get("meters")
    if not isinstance(rows, list):
        return None
    result = {}
    for row in rows:
        if not isinstance(row, dict) or row.get("meter") not in ("assistant", "study_ai"):
            return None
        used = row.get("used")
        if isinstance(used, bool) or not isinstance(used, (int, float)) or not math.isfinite(used) or used < 0:
            return None
        if row["meter"] in result:
            return None
        result[row["meter"]] = used
    return result if set(result) == {"assistant", "study_ai"} else None


def summary(
    baseline: dict[str, object] | None,
    latest: dict[str, object],
    budget_usd: float,
) -> dict[str, object]:
    """Account-window credits, explicitly separate from a scenario dollar budget."""
    warnings = []
    delta = None
    if latest.get("status") != "available":
        warnings.append("Account usage is unavailable; it is not zero usage.")
    if not baseline or baseline.get("status") != "available":
        warnings.append("No successful pre-submission usage baseline; the scenario delta is unavailable.")
    elif latest.get("status") == "available":
        before, after = _meters(baseline), _meters(latest)
        old_pricing, new_pricing = baseline.get("pricing"), latest.get("pricing")
        same_rate = (
            isinstance(old_pricing, dict) and isinstance(new_pricing, dict)
            and isinstance(old_pricing.get("rate_version"), str)
            and bool(old_pricing["rate_version"])
            and old_pricing["rate_version"] == new_pricing.get("rate_version")
        )
        if not baseline.get("week") or baseline["week"] != latest.get("week") or not same_rate:
            warnings.append("Usage week or rate version changed or is missing; the delta is unavailable.")
        elif before is None or after is None or any(after[key] < before[key] for key in before):
            warnings.append("Usage counters are missing, invalid or decreased; the delta is unavailable.")
        else:
            delta = {key: after[key] - before[key] for key in before}
    if latest.get("status") == "available":
        measurement, pricing = latest.get("measurement"), latest.get("pricing")
        if (
            not isinstance(measurement, dict)
            or measurement.get("unreported") != 0 or measurement.get("unrated") != 0
            or not isinstance(pricing, dict) or pricing.get("provisional") is not False
        ):
            warnings.append("Usage pricing or measurement is incomplete/provisional; measured credits can undercount.")
        for row in latest.get("meters", []):
            if isinstance(row, dict) and row.get("state") in ("near", "reached"):
                warnings.append(f"Weekly account {row.get('meter')} allowance: {row['state']} (not a scenario dollar limit).")
    warnings.append(
        f"The ${budget_usd:g} scenario budget is warning-only. The usage API returns credits without a USD conversion; dollar comparison is unavailable."
    )
    return {
        "endpoint": USAGE_PATH,
        "scope": "Account-wide window; includes other account activity and asynchronously ingested usage. Not exact scenario or family attribution.",
        "unit": "credits",
        "delta": delta,
        "budget_usd": budget_usd,
        "budget_mode": "warn",
        "budget_status": "unavailable",
        "cost_usd": None,
        "warnings": warnings,
    }
