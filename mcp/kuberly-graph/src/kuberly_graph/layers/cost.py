"""CostLayer — AWS Cost Explorer monthly snapshot.

Auth-gated. ``boto3`` is **optional** — wrapped in try/except so the layer
soft-degrades to ``([], [])`` when the SDK is missing or AWS creds aren't
configured. Never crashes.

Required AWS setup (when boto3 IS available):
  * ``aws sso login`` (or static keys via ``AWS_PROFILE`` / env vars).
  * IAM ``ce:GetCostAndUsage`` on the calling account.
  * Cost Explorer must be enabled in the AWS account (one-time opt-in).

ctx knobs (all optional):
  * ``aws_account_id``         — defaults to ``sts.GetCallerIdentity``.
  * ``cost_lookback_months``   — defaults to 3.
  * ``cost_granularity``       — defaults to ``MONTHLY``.

Nodes (when working):
  * ``cost_period:<env>/<account>/<YYYY-MM>``
  * ``cost_service:<env>/<account>/<YYYY-MM>/<aws-service>``

No edges to other layers in v1 — resource-level cost attribution requires the
paid Cost Allocation tag tier and is deferred. Documented as a Phase 7D+
extension.
"""

from __future__ import annotations

import datetime as _dt
import sys
from pathlib import Path

from .base import Layer


def _log(verbose: bool, msg: str) -> None:
    if verbose:
        print(f"  [CostLayer] {msg}", file=sys.stderr)


class CostLayer(Layer):
    name = "cost"
    refresh_trigger = "manual"

    def scan(self, ctx: dict) -> tuple[list[dict], list[dict]]:
        verbose = bool(ctx.get("verbose"))

        try:
            import boto3  # type: ignore
            from botocore.exceptions import (  # type: ignore
                BotoCoreError,
                ClientError,
                NoCredentialsError,
            )
        except Exception as exc:  # noqa: BLE001
            _log(verbose, f"boto3 unavailable ({exc}); soft-degrading to 0/0")
            return [], []

        lookback = int(ctx.get("cost_lookback_months") or 3)
        granularity = str(ctx.get("cost_granularity") or "MONTHLY").upper()
        account_id = str(ctx.get("aws_account_id") or "")

        try:
            session = boto3.Session()
            if not account_id:
                try:
                    sts = session.client("sts")
                    account_id = str(sts.get_caller_identity().get("Account") or "")
                except (NoCredentialsError, BotoCoreError, ClientError) as exc:
                    _log(verbose, f"sts.get_caller_identity failed ({exc}); soft-degrading")
                    return [], []
            ce = session.client("ce", region_name="us-east-1")
        except Exception as exc:  # noqa: BLE001
            _log(verbose, f"boto3 session creation failed ({exc}); soft-degrading")
            return [], []

        # Compute a monthly date range that includes the last `lookback` complete
        # months plus the current month. Cost Explorer end is exclusive.
        today = _dt.date.today()
        start_month = today.replace(day=1)
        for _ in range(lookback):
            prev = start_month - _dt.timedelta(days=1)
            start_month = prev.replace(day=1)
        end_month = (today.replace(day=1) + _dt.timedelta(days=32)).replace(day=1)
        time_period = {"Start": start_month.isoformat(), "End": end_month.isoformat()}

        try:
            resp = ce.get_cost_and_usage(
                TimePeriod=time_period,
                Granularity=granularity,
                Metrics=["UnblendedCost", "BlendedCost"],
                GroupBy=[{"Type": "DIMENSION", "Key": "SERVICE"}],
            )
        except (NoCredentialsError, BotoCoreError, ClientError) as exc:
            _log(verbose, f"Cost Explorer call failed ({exc}); soft-degrading")
            return [], []
        except Exception as exc:  # noqa: BLE001
            _log(verbose, f"Cost Explorer unexpected failure ({exc}); soft-degrading")
            return [], []

        env = str(ctx.get("cost_env_label") or "aws")  # synthetic env tag

        nodes: list[dict] = []
        edges: list[dict] = []

        results_by_time = resp.get("ResultsByTime") or []
        for entry in results_by_time:
            if not isinstance(entry, dict):
                continue
            period = entry.get("TimePeriod") or {}
            month_key = str(period.get("Start") or "")[:7]  # YYYY-MM
            if not month_key:
                continue
            period_id = f"cost_period:{env}/{account_id}/{month_key}"
            total_unblended = 0.0
            total_blended = 0.0
            currency = ""
            groups = entry.get("Groups") or []
            for g in groups:
                if not isinstance(g, dict):
                    continue
                keys = g.get("Keys") or []
                svc = str(keys[0]) if keys else ""
                metrics = g.get("Metrics") or {}
                ub = metrics.get("UnblendedCost") or {}
                bl = metrics.get("BlendedCost") or {}
                amt = float(ub.get("Amount") or 0.0)
                bamt = float(bl.get("Amount") or 0.0)
                currency = str(ub.get("Unit") or currency or "USD")
                total_unblended += amt
                total_blended += bamt
                if not svc:
                    continue
                svc_id = f"cost_service:{env}/{account_id}/{month_key}/{svc}"
                nodes.append(
                    {
                        "id": svc_id,
                        "type": "cost_service",
                        "label": f"{svc} {month_key}",
                        "env": env,
                        "account_id": account_id,
                        "month": month_key,
                        "service": svc,
                        "usd": amt,
                        "blended_cost": bamt,
                        "currency": currency,
                    }
                )
                edges.append(
                    {
                        "source": svc_id,
                        "target": period_id,
                        "relation": "in_period",
                    }
                )
            nodes.append(
                {
                    "id": period_id,
                    "type": "cost_period",
                    "label": f"{account_id} {month_key}",
                    "env": env,
                    "account_id": account_id,
                    "month": month_key,
                    "total_usd": total_unblended,
                    "total_blended_usd": total_blended,
                    "granularity": granularity,
                    "currency": currency or "USD",
                }
            )

        _log(verbose, f"emitted {len(nodes)} nodes / {len(edges)} edges")
        return nodes, edges
