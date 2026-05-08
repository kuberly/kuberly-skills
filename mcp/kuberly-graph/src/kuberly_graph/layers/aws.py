"""AwsLayer — direct boto3 scan of ~25 AWS services.

Auth-gated. ``boto3`` is **optional** — wrapped in try/except so the layer
soft-degrades to ``([], [])`` when the SDK is missing or AWS creds aren't
configured. Never crashes.

Required AWS setup (when boto3 IS available):
  * ``aws sso login`` (or static keys via ``AWS_PROFILE`` / env vars).
  * ReadOnlyAccess (or per-service describe/list) on the calling account.

ctx knobs (all optional):
  * ``aws_region``              — defaults to ``$AWS_REGION`` env / ``us-east-1``.
  * ``aws_account_id``          — auto-detected via ``sts.GetCallerIdentity``.
  * ``aws_per_service_limit``   — cap per service call (default 1000).
  * ``aws_services``            — filter to a subset of service names.

Nodes emitted (all under ``aws:*`` namespace, parallel to existing
network/iam/storage/dns layers — they read from tfstate; we read live AWS).
See README in the work-order for the full table. Each node carries
``layer="aws"`` so dashboard/api categorises them as the orange ``aws``
bucket.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from .base import Layer


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _log(verbose: bool, msg: str) -> None:
    if verbose:
        print(f"  [AwsLayer] {msg}", file=sys.stderr)


def _warn(msg: str) -> None:
    """Always emit a warning to stderr, regardless of verbose flag."""
    print(f"  [AwsLayer] WARN {msg}", file=sys.stderr)


def _tags_to_dict(tags) -> dict:
    """boto3 returns tags as ``[{"Key":..., "Value":...}, ...]``. Normalize."""
    if not isinstance(tags, list):
        return {}
    out: dict = {}
    for t in tags:
        if isinstance(t, dict):
            k = t.get("Key") or t.get("key")
            v = t.get("Value") or t.get("value")
            if isinstance(k, str):
                out[k] = v if isinstance(v, (str, int, float, bool)) else str(v)
    return out


def _tag_name(tags_dict: dict, fallback: str = "") -> str:
    if not isinstance(tags_dict, dict):
        return fallback
    return str(tags_dict.get("Name") or fallback)


def _short_proto(p) -> str:
    if p is None or p == "" or p == -1 or p == "-1":
        return "any"
    return str(p)


def _ingress_summary(perms) -> list[str]:
    """Summarise IpPermissions list to compact strings."""
    if not isinstance(perms, list):
        return []
    out: list[str] = []
    for p in perms[:32]:
        if not isinstance(p, dict):
            continue
        proto = _short_proto(p.get("IpProtocol"))
        from_p = p.get("FromPort")
        to_p = p.get("ToPort")
        port_part = ""
        if from_p == to_p and from_p is not None:
            port_part = str(from_p)
        elif from_p is not None and to_p is not None:
            port_part = f"{from_p}-{to_p}"
        cidrs = []
        for r in p.get("IpRanges") or []:
            if isinstance(r, dict):
                c = r.get("CidrIp")
                if c:
                    cidrs.append(str(c))
        for r in p.get("Ipv6Ranges") or []:
            if isinstance(r, dict):
                c = r.get("CidrIpv6")
                if c:
                    cidrs.append(str(c))
        sgs = []
        for r in p.get("UserIdGroupPairs") or []:
            if isinstance(r, dict):
                g = r.get("GroupId")
                if g:
                    sgs.append(str(g))
        sources = ",".join(cidrs + sgs) or "*"
        out.append(f"{proto}/{port_part}<-{sources}")
    return out


# ---------------------------------------------------------------------------
# AwsLayer
# ---------------------------------------------------------------------------


# Order roughly: core network, then compute, then identity, then data plane.
_DEFAULT_SERVICES = [
    "sts",
    "vpc",
    "subnet",
    "sg",
    "rtb",
    "nat",
    "igw",
    "vpce",
    "ebs",
    "ec2",
    "eks",
    "iam_role",
    "iam_policy",
    "iam_instance_profile",
    "s3",
    "rds_cluster",
    "rds_instance",
    "elasticache",
    "ecr",
    "lambda",
    "lb",
    "cloudfront",
    "r53",
    "acm",
    "cw_logs",
]


class AwsLayer(Layer):
    name = "aws"
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
            _warn(f"boto3 unavailable ({exc}); soft-degrading to 0/0")
            return [], []

        region = str(
            ctx.get("aws_region")
            or os.environ.get("AWS_REGION")
            or os.environ.get("AWS_DEFAULT_REGION")
            or "us-east-1"
        )
        per_limit = int(ctx.get("aws_per_service_limit") or 1000)
        services_filter = ctx.get("aws_services") or _DEFAULT_SERVICES
        if isinstance(services_filter, str):
            services_filter = [services_filter]
        services_set = set(services_filter)

        try:
            session = boto3.Session(region_name=region)
        except Exception as exc:  # noqa: BLE001
            _warn(f"boto3 session creation failed ({exc}); soft-degrading")
            return [], []

        # Validate creds via STS up front.
        account_id = str(ctx.get("aws_account_id") or "")
        try:
            sts = session.client("sts")
            ident = sts.get_caller_identity()
            if not account_id:
                account_id = str(ident.get("Account") or "")
        except (NoCredentialsError, BotoCoreError, ClientError) as exc:
            _warn(f"sts.get_caller_identity failed ({exc}); soft-degrading to 0/0")
            return [], []
        except Exception as exc:  # noqa: BLE001
            _warn(f"sts unexpected failure ({exc}); soft-degrading to 0/0")
            return [], []

        nodes: list[dict] = []
        edges: list[dict] = []
        emitted_ids: set[str] = set()
        edge_keys: set[tuple[str, str, str]] = set()

        def _emit_node(node: dict) -> None:
            nid = node.get("id")
            if not nid or nid in emitted_ids:
                return
            emitted_ids.add(nid)
            node.setdefault("layer", "aws")
            node.setdefault("region", region)
            node.setdefault("account_id", account_id)
            nodes.append(node)

        def _emit_edge(source: str, target: str, relation: str, **extra) -> None:
            if not source or not target:
                return
            key = (source, target, relation)
            if key in edge_keys:
                return
            edge_keys.add(key)
            edge = {"source": source, "target": target, "relation": relation, "layer": "aws"}
            edge.update(extra)
            edges.append(edge)

        # ---- helper: paginate a method up to per_limit -----------------------
        def _paginate(client, method_name, key, **kwargs):
            try:
                if client.can_paginate(method_name):
                    paginator = client.get_paginator(method_name)
                    out: list = []
                    for page in paginator.paginate(**kwargs):
                        for item in page.get(key) or []:
                            out.append(item)
                            if len(out) >= per_limit:
                                return out
                    return out
                resp = getattr(client, method_name)(**kwargs)
                return list((resp.get(key) or []))[:per_limit]
            except (NoCredentialsError, BotoCoreError, ClientError) as exc:
                _warn(f"{method_name} failed ({exc}); skipping")
                return []
            except Exception as exc:  # noqa: BLE001
                _warn(f"{method_name} unexpected failure ({exc}); skipping")
                return []

        # ============== 1. STS (account node) ================================
        if "sts" in services_set:
            alias = ""
            try:
                iam = session.client("iam")
                aliases = iam.list_account_aliases().get("AccountAliases") or []
                alias = str(aliases[0]) if aliases else ""
            except Exception as exc:  # noqa: BLE001
                _log(verbose, f"list_account_aliases failed ({exc})")
            _emit_node(
                {
                    "id": f"aws:account:{account_id}",
                    "type": "aws_account",
                    "label": alias or account_id,
                    "alias": alias,
                }
            )

        # ============== 2. VPC ================================================
        try:
            ec2 = session.client("ec2")
        except Exception as exc:  # noqa: BLE001
            _warn(f"ec2 client init failed ({exc}); skipping ec2 family")
            ec2 = None  # type: ignore

        if ec2 is not None and "vpc" in services_set:
            for v in _paginate(ec2, "describe_vpcs", "Vpcs"):
                if not isinstance(v, dict):
                    continue
                vpc_id = str(v.get("VpcId") or "")
                if not vpc_id:
                    continue
                tags = _tags_to_dict(v.get("Tags"))
                _emit_node(
                    {
                        "id": f"aws:vpc:{vpc_id}",
                        "type": "aws_vpc",
                        "label": _tag_name(tags, vpc_id),
                        "vpc_id": vpc_id,
                        "cidr": str(v.get("CidrBlock") or ""),
                        "is_default": bool(v.get("IsDefault")),
                        "state": str(v.get("State") or ""),
                        "tags": tags,
                    }
                )

        # ============== 3. Subnet =============================================
        if ec2 is not None and "subnet" in services_set:
            for s in _paginate(ec2, "describe_subnets", "Subnets"):
                if not isinstance(s, dict):
                    continue
                sub_id = str(s.get("SubnetId") or "")
                if not sub_id:
                    continue
                vpc_id = str(s.get("VpcId") or "")
                tags = _tags_to_dict(s.get("Tags"))
                _emit_node(
                    {
                        "id": f"aws:subnet:{sub_id}",
                        "type": "aws_subnet",
                        "label": _tag_name(tags, sub_id),
                        "subnet_id": sub_id,
                        "vpc_id": vpc_id,
                        "az": str(s.get("AvailabilityZone") or ""),
                        "cidr": str(s.get("CidrBlock") or ""),
                        "public": bool(s.get("MapPublicIpOnLaunch")),
                        "tags": tags,
                    }
                )
                if vpc_id:
                    _emit_edge(f"aws:subnet:{sub_id}", f"aws:vpc:{vpc_id}", "in_vpc")

        # ============== 4. Security Group =====================================
        if ec2 is not None and "sg" in services_set:
            for sg in _paginate(ec2, "describe_security_groups", "SecurityGroups"):
                if not isinstance(sg, dict):
                    continue
                sg_id = str(sg.get("GroupId") or "")
                if not sg_id:
                    continue
                vpc_id = str(sg.get("VpcId") or "")
                _emit_node(
                    {
                        "id": f"aws:sg:{sg_id}",
                        "type": "aws_sg",
                        "label": str(sg.get("GroupName") or sg_id),
                        "sg_id": sg_id,
                        "vpc_id": vpc_id,
                        "description": str(sg.get("Description") or ""),
                        "ingress_summary": _ingress_summary(sg.get("IpPermissions")),
                        "egress_summary": _ingress_summary(sg.get("IpPermissionsEgress")),
                        "tags": _tags_to_dict(sg.get("Tags")),
                    }
                )
                if vpc_id:
                    _emit_edge(f"aws:sg:{sg_id}", f"aws:vpc:{vpc_id}", "in_vpc")

        # ============== 5. Route Table ========================================
        if ec2 is not None and "rtb" in services_set:
            for r in _paginate(ec2, "describe_route_tables", "RouteTables"):
                if not isinstance(r, dict):
                    continue
                rtb_id = str(r.get("RouteTableId") or "")
                if not rtb_id:
                    continue
                vpc_id = str(r.get("VpcId") or "")
                routes = r.get("Routes") or []
                summary: list[str] = []
                for rt in routes[:16]:
                    if not isinstance(rt, dict):
                        continue
                    dst = (
                        rt.get("DestinationCidrBlock")
                        or rt.get("DestinationIpv6CidrBlock")
                        or rt.get("DestinationPrefixListId")
                        or ""
                    )
                    tgt = (
                        rt.get("GatewayId")
                        or rt.get("NatGatewayId")
                        or rt.get("InstanceId")
                        or rt.get("TransitGatewayId")
                        or rt.get("VpcPeeringConnectionId")
                        or ""
                    )
                    if dst:
                        summary.append(f"{dst}->{tgt}")
                _emit_node(
                    {
                        "id": f"aws:rtb:{rtb_id}",
                        "type": "aws_rtb",
                        "label": rtb_id,
                        "rtb_id": rtb_id,
                        "vpc_id": vpc_id,
                        "routes_summary": summary,
                        "tags": _tags_to_dict(r.get("Tags")),
                    }
                )
                if vpc_id:
                    _emit_edge(f"aws:rtb:{rtb_id}", f"aws:vpc:{vpc_id}", "in_vpc")

        # ============== 6. NAT Gateways =======================================
        if ec2 is not None and "nat" in services_set:
            for n in _paginate(ec2, "describe_nat_gateways", "NatGateways"):
                if not isinstance(n, dict):
                    continue
                nat_id = str(n.get("NatGatewayId") or "")
                if not nat_id:
                    continue
                subnet_id = str(n.get("SubnetId") or "")
                vpc_id = str(n.get("VpcId") or "")
                _emit_node(
                    {
                        "id": f"aws:nat:{nat_id}",
                        "type": "aws_nat",
                        "label": nat_id,
                        "nat_id": nat_id,
                        "vpc_id": vpc_id,
                        "subnet_id": subnet_id,
                        "state": str(n.get("State") or ""),
                        "tags": _tags_to_dict(n.get("Tags")),
                    }
                )
                if subnet_id:
                    _emit_edge(f"aws:nat:{nat_id}", f"aws:subnet:{subnet_id}", "lives_in")

        # ============== 7. IGW ================================================
        if ec2 is not None and "igw" in services_set:
            for ig in _paginate(ec2, "describe_internet_gateways", "InternetGateways"):
                if not isinstance(ig, dict):
                    continue
                ig_id = str(ig.get("InternetGatewayId") or "")
                if not ig_id:
                    continue
                attachments = []
                for a in ig.get("Attachments") or []:
                    if isinstance(a, dict):
                        attachments.append(str(a.get("VpcId") or ""))
                _emit_node(
                    {
                        "id": f"aws:igw:{ig_id}",
                        "type": "aws_igw",
                        "label": ig_id,
                        "igw_id": ig_id,
                        "vpc_attachments": [a for a in attachments if a],
                        "tags": _tags_to_dict(ig.get("Tags")),
                    }
                )
                for vpc_id in attachments:
                    if vpc_id:
                        _emit_edge(f"aws:igw:{ig_id}", f"aws:vpc:{vpc_id}", "in_vpc")

        # ============== 8. VPC Endpoint =======================================
        if ec2 is not None and "vpce" in services_set:
            for v in _paginate(ec2, "describe_vpc_endpoints", "VpcEndpoints"):
                if not isinstance(v, dict):
                    continue
                vpce_id = str(v.get("VpcEndpointId") or "")
                if not vpce_id:
                    continue
                vpc_id = str(v.get("VpcId") or "")
                _emit_node(
                    {
                        "id": f"aws:vpce:{vpce_id}",
                        "type": "aws_vpce",
                        "label": vpce_id,
                        "vpce_id": vpce_id,
                        "vpc_id": vpc_id,
                        "service_name": str(v.get("ServiceName") or ""),
                        "vpce_type": str(v.get("VpcEndpointType") or ""),
                        "state": str(v.get("State") or ""),
                        "tags": _tags_to_dict(v.get("Tags")),
                    }
                )
                if vpc_id:
                    _emit_edge(f"aws:vpce:{vpce_id}", f"aws:vpc:{vpc_id}", "in_vpc")

        # ============== 9. EBS Volumes ========================================
        if ec2 is not None and "ebs" in services_set:
            for v in _paginate(ec2, "describe_volumes", "Volumes"):
                if not isinstance(v, dict):
                    continue
                vol_id = str(v.get("VolumeId") or "")
                if not vol_id:
                    continue
                attach_ids: list[str] = []
                for a in v.get("Attachments") or []:
                    if isinstance(a, dict):
                        i = str(a.get("InstanceId") or "")
                        if i:
                            attach_ids.append(i)
                _emit_node(
                    {
                        "id": f"aws:ebs:{vol_id}",
                        "type": "aws_ebs",
                        "label": vol_id,
                        "ebs_id": vol_id,
                        "size_gb": int(v.get("Size") or 0),
                        "volume_type": str(v.get("VolumeType") or ""),
                        "az": str(v.get("AvailabilityZone") or ""),
                        "encrypted": bool(v.get("Encrypted")),
                        "state": str(v.get("State") or ""),
                        "attachments": attach_ids,
                        "tags": _tags_to_dict(v.get("Tags")),
                    }
                )
                for inst in attach_ids:
                    _emit_edge(f"aws:ebs:{vol_id}", f"aws:ec2:{inst}", "attached_to")

        # ============== 10. EC2 Instances =====================================
        if ec2 is not None and "ec2" in services_set:
            try:
                paginator = ec2.get_paginator("describe_instances")
                seen = 0
                stop = False
                for page in paginator.paginate():
                    for r in page.get("Reservations") or []:
                        for i in r.get("Instances") or []:
                            if not isinstance(i, dict):
                                continue
                            iid = str(i.get("InstanceId") or "")
                            if not iid:
                                continue
                            vpc_id = str(i.get("VpcId") or "")
                            sub_id = str(i.get("SubnetId") or "")
                            sg_ids = [
                                str(g.get("GroupId") or "")
                                for g in i.get("SecurityGroups") or []
                                if isinstance(g, dict)
                            ]
                            sg_ids = [s for s in sg_ids if s]
                            tags = _tags_to_dict(i.get("Tags"))
                            _emit_node(
                                {
                                    "id": f"aws:ec2:{iid}",
                                    "type": "aws_ec2",
                                    "label": _tag_name(tags, iid),
                                    "instance_id": iid,
                                    "instance_type": str(i.get("InstanceType") or ""),
                                    "az": str(
                                        (i.get("Placement") or {}).get("AvailabilityZone") or ""
                                    ),
                                    "vpc_id": vpc_id,
                                    "subnet_id": sub_id,
                                    "security_groups": sg_ids,
                                    "state": str((i.get("State") or {}).get("Name") or ""),
                                    "private_ip": str(i.get("PrivateIpAddress") or ""),
                                    "tags": tags,
                                }
                            )
                            if vpc_id:
                                _emit_edge(f"aws:ec2:{iid}", f"aws:vpc:{vpc_id}", "in_vpc")
                            if sub_id:
                                _emit_edge(f"aws:ec2:{iid}", f"aws:subnet:{sub_id}", "in_subnet")
                            for sg in sg_ids:
                                _emit_edge(f"aws:ec2:{iid}", f"aws:sg:{sg}", "uses_sg")
                            seen += 1
                            if seen >= per_limit:
                                stop = True
                                break
                        if stop:
                            break
                    if stop:
                        break
            except (NoCredentialsError, BotoCoreError, ClientError) as exc:
                _warn(f"describe_instances failed ({exc}); skipping")
            except Exception as exc:  # noqa: BLE001
                _warn(f"describe_instances unexpected failure ({exc}); skipping")

        # ============== 11. EKS Clusters + Nodegroups =========================
        if "eks" in services_set:
            try:
                eks = session.client("eks")
                cluster_names: list[str] = []
                try:
                    paginator = eks.get_paginator("list_clusters")
                    for page in paginator.paginate():
                        cluster_names.extend(page.get("clusters") or [])
                        if len(cluster_names) >= per_limit:
                            cluster_names = cluster_names[:per_limit]
                            break
                except (NoCredentialsError, BotoCoreError, ClientError) as exc:
                    _warn(f"eks.list_clusters failed ({exc}); skipping eks")
                    cluster_names = []
                for name in cluster_names:
                    try:
                        c = eks.describe_cluster(name=name).get("cluster") or {}
                    except (NoCredentialsError, BotoCoreError, ClientError) as exc:
                        _warn(f"eks.describe_cluster({name}) failed ({exc}); skipping")
                        continue
                    rn = c.get("resourcesVpcConfig") or {}
                    vpc_id = str(rn.get("vpcId") or "")
                    subnets = [s for s in (rn.get("subnetIds") or []) if isinstance(s, str)]
                    role_arn = str(c.get("roleArn") or "")
                    _emit_node(
                        {
                            "id": f"aws:eks:{name}",
                            "type": "aws_eks",
                            "label": name,
                            "cluster_name": name,
                            "version": str(c.get("version") or ""),
                            "vpc_id": vpc_id,
                            "subnets": subnets,
                            "role_arn": role_arn,
                            "status": str(c.get("status") or ""),
                            "endpoint": str(c.get("endpoint") or ""),
                        }
                    )
                    if vpc_id:
                        _emit_edge(f"aws:eks:{name}", f"aws:vpc:{vpc_id}", "in_vpc")
                    for s in subnets:
                        _emit_edge(f"aws:eks:{name}", f"aws:subnet:{s}", "uses_subnet")
                    if role_arn:
                        _emit_edge(f"aws:eks:{name}", f"aws:iam_role:{role_arn}", "uses_role")

                    # Nodegroups
                    try:
                        ng_pag = eks.get_paginator("list_nodegroups")
                        ng_names: list[str] = []
                        for page in ng_pag.paginate(clusterName=name):
                            ng_names.extend(page.get("nodegroups") or [])
                            if len(ng_names) >= per_limit:
                                ng_names = ng_names[:per_limit]
                                break
                    except (NoCredentialsError, BotoCoreError, ClientError) as exc:
                        _warn(f"eks.list_nodegroups({name}) failed ({exc}); skipping")
                        ng_names = []
                    for ng_name in ng_names:
                        try:
                            ng = eks.describe_nodegroup(
                                clusterName=name, nodegroupName=ng_name
                            ).get("nodegroup") or {}
                        except (NoCredentialsError, BotoCoreError, ClientError) as exc:
                            _warn(
                                f"eks.describe_nodegroup({name}/{ng_name}) failed ({exc}); skipping"
                            )
                            continue
                        ng_id = f"aws:eks_nodegroup:{name}/{ng_name}"
                        ng_subnets = [
                            s for s in (ng.get("subnets") or []) if isinstance(s, str)
                        ]
                        scaling = ng.get("scalingConfig") or {}
                        _emit_node(
                            {
                                "id": ng_id,
                                "type": "aws_eks_nodegroup",
                                "label": f"{name}/{ng_name}",
                                "cluster_name": name,
                                "nodegroup_name": ng_name,
                                "instance_types": list(ng.get("instanceTypes") or []),
                                "capacity_type": str(ng.get("capacityType") or ""),
                                "desired_size": int(scaling.get("desiredSize") or 0),
                                "min_size": int(scaling.get("minSize") or 0),
                                "max_size": int(scaling.get("maxSize") or 0),
                                "subnets": ng_subnets,
                                "status": str(ng.get("status") or ""),
                            }
                        )
                        _emit_edge(ng_id, f"aws:eks:{name}", "member_of")
                        for s in ng_subnets:
                            _emit_edge(ng_id, f"aws:subnet:{s}", "uses_subnet")
            except Exception as exc:  # noqa: BLE001
                _warn(f"eks family unexpected failure ({exc}); skipping")

        # ============== 12. IAM Roles =========================================
        if "iam_role" in services_set:
            try:
                iam = session.client("iam")
                roles = _paginate(iam, "list_roles", "Roles")
                for r in roles:
                    if not isinstance(r, dict):
                        continue
                    arn = str(r.get("Arn") or "")
                    if not arn:
                        continue
                    role_name = str(r.get("RoleName") or "")
                    # attached managed policies
                    attached: list[str] = []
                    try:
                        for page in iam.get_paginator("list_attached_role_policies").paginate(
                            RoleName=role_name
                        ):
                            for p in page.get("AttachedPolicies") or []:
                                if isinstance(p, dict):
                                    pa = str(p.get("PolicyArn") or "")
                                    if pa:
                                        attached.append(pa)
                            if len(attached) >= 50:
                                break
                    except (NoCredentialsError, BotoCoreError, ClientError) as exc:
                        _log(verbose, f"list_attached_role_policies({role_name}): {exc}")
                    inline: list[str] = []
                    try:
                        for page in iam.get_paginator("list_role_policies").paginate(
                            RoleName=role_name
                        ):
                            for pn in page.get("PolicyNames") or []:
                                if isinstance(pn, str):
                                    inline.append(pn)
                            if len(inline) >= 50:
                                break
                    except (NoCredentialsError, BotoCoreError, ClientError) as exc:
                        _log(verbose, f"list_role_policies({role_name}): {exc}")
                    _emit_node(
                        {
                            "id": f"aws:iam_role:{arn}",
                            "type": "aws_iam_role",
                            "label": role_name or arn,
                            "arn": arn,
                            "role_name": role_name,
                            "path": str(r.get("Path") or "/"),
                            "max_session_duration": int(r.get("MaxSessionDuration") or 0),
                            "attached_policies": attached,
                            "inline_policies": inline,
                        }
                    )
                    for pa in attached:
                        _emit_edge(f"aws:iam_role:{arn}", f"aws:iam_policy:{pa}", "attaches")
            except Exception as exc:  # noqa: BLE001
                _warn(f"iam roles family unexpected failure ({exc}); skipping")

        # ============== 13. IAM Policies (customer-managed) ===================
        if "iam_policy" in services_set:
            try:
                iam = session.client("iam")
                pols = _paginate(iam, "list_policies", "Policies", Scope="Local")
                for p in pols:
                    if not isinstance(p, dict):
                        continue
                    arn = str(p.get("Arn") or "")
                    if not arn:
                        continue
                    _emit_node(
                        {
                            "id": f"aws:iam_policy:{arn}",
                            "type": "aws_iam_policy",
                            "label": str(p.get("PolicyName") or arn),
                            "arn": arn,
                            "policy_name": str(p.get("PolicyName") or ""),
                            "path": str(p.get("Path") or "/"),
                            "attachment_count": int(p.get("AttachmentCount") or 0),
                            "is_attachable": bool(p.get("IsAttachable")),
                        }
                    )
            except Exception as exc:  # noqa: BLE001
                _warn(f"iam policies family unexpected failure ({exc}); skipping")

        # ============== 14. IAM Instance Profiles =============================
        if "iam_instance_profile" in services_set:
            try:
                iam = session.client("iam")
                profs = _paginate(iam, "list_instance_profiles", "InstanceProfiles")
                for p in profs:
                    if not isinstance(p, dict):
                        continue
                    name = str(p.get("InstanceProfileName") or "")
                    if not name:
                        continue
                    role_arns = [
                        str(r.get("Arn") or "")
                        for r in p.get("Roles") or []
                        if isinstance(r, dict)
                    ]
                    role_arns = [r for r in role_arns if r]
                    pid = f"aws:iam_instance_profile:{name}"
                    _emit_node(
                        {
                            "id": pid,
                            "type": "aws_iam_instance_profile",
                            "label": name,
                            "profile_name": name,
                            "arn": str(p.get("Arn") or ""),
                            "role_arns": role_arns,
                        }
                    )
                    for ra in role_arns:
                        _emit_edge(pid, f"aws:iam_role:{ra}", "uses_role")
            except Exception as exc:  # noqa: BLE001
                _warn(f"iam instance profiles unexpected failure ({exc}); skipping")

        # ============== 15. S3 Buckets ========================================
        if "s3" in services_set:
            try:
                s3 = session.client("s3")
                buckets = []
                try:
                    buckets = (s3.list_buckets().get("Buckets") or [])[:per_limit]
                except (NoCredentialsError, BotoCoreError, ClientError) as exc:
                    _warn(f"s3.list_buckets failed ({exc}); skipping")
                for b in buckets:
                    if not isinstance(b, dict):
                        continue
                    name = str(b.get("Name") or "")
                    if not name:
                        continue
                    bucket_region = ""
                    try:
                        loc = s3.get_bucket_location(Bucket=name)
                        bucket_region = str(loc.get("LocationConstraint") or "us-east-1")
                    except (NoCredentialsError, BotoCoreError, ClientError):
                        pass
                    encryption = ""
                    try:
                        enc = s3.get_bucket_encryption(Bucket=name)
                        rules = (
                            (enc.get("ServerSideEncryptionConfiguration") or {}).get("Rules")
                            or []
                        )
                        if rules:
                            sse = (rules[0] or {}).get(
                                "ApplyServerSideEncryptionByDefault"
                            ) or {}
                            encryption = str(sse.get("SSEAlgorithm") or "")
                    except (NoCredentialsError, BotoCoreError, ClientError):
                        pass
                    versioning = ""
                    try:
                        versioning = str(
                            s3.get_bucket_versioning(Bucket=name).get("Status") or ""
                        )
                    except (NoCredentialsError, BotoCoreError, ClientError):
                        pass
                    has_policy = False
                    try:
                        s3.get_bucket_policy(Bucket=name)
                        has_policy = True
                    except (NoCredentialsError, BotoCoreError, ClientError):
                        has_policy = False
                    public_block = {}
                    try:
                        pb = s3.get_public_access_block(Bucket=name)
                        public_block = pb.get("PublicAccessBlockConfiguration") or {}
                    except (NoCredentialsError, BotoCoreError, ClientError):
                        public_block = {}
                    _emit_node(
                        {
                            "id": f"aws:s3:{name}",
                            "type": "aws_s3",
                            "label": name,
                            "bucket_name": name,
                            "bucket_region": bucket_region,
                            "encryption": encryption,
                            "versioning": versioning,
                            "has_policy": has_policy,
                            "public_block": dict(public_block),
                        }
                    )
            except Exception as exc:  # noqa: BLE001
                _warn(f"s3 family unexpected failure ({exc}); skipping")

        # ============== 16. RDS Clusters ======================================
        if "rds_cluster" in services_set:
            try:
                rds = session.client("rds")
                for c in _paginate(rds, "describe_db_clusters", "DBClusters"):
                    if not isinstance(c, dict):
                        continue
                    cid = str(c.get("DBClusterIdentifier") or "")
                    if not cid:
                        continue
                    members = [
                        str(m.get("DBInstanceIdentifier") or "")
                        for m in c.get("DBClusterMembers") or []
                        if isinstance(m, dict)
                    ]
                    _emit_node(
                        {
                            "id": f"aws:rds_cluster:{cid}",
                            "type": "aws_rds_cluster",
                            "label": cid,
                            "cluster_id": cid,
                            "engine": str(c.get("Engine") or ""),
                            "engine_version": str(c.get("EngineVersion") or ""),
                            "endpoint": str(c.get("Endpoint") or ""),
                            "port": int(c.get("Port") or 0),
                            "multi_az": bool(c.get("MultiAZ")),
                            "instance_ids": members,
                            "status": str(c.get("Status") or ""),
                        }
                    )
                    for m in members:
                        _emit_edge(
                            f"aws:rds_cluster:{cid}", f"aws:rds_instance:{m}", "has_member"
                        )
            except Exception as exc:  # noqa: BLE001
                _warn(f"rds clusters unexpected failure ({exc}); skipping")

        # ============== 17. RDS Instances =====================================
        if "rds_instance" in services_set:
            try:
                rds = session.client("rds")
                for i in _paginate(rds, "describe_db_instances", "DBInstances"):
                    if not isinstance(i, dict):
                        continue
                    iid = str(i.get("DBInstanceIdentifier") or "")
                    if not iid:
                        continue
                    cid = str(i.get("DBClusterIdentifier") or "")
                    _emit_node(
                        {
                            "id": f"aws:rds_instance:{iid}",
                            "type": "aws_rds_instance",
                            "label": iid,
                            "instance_id": iid,
                            "engine": str(i.get("Engine") or ""),
                            "instance_class": str(i.get("DBInstanceClass") or ""),
                            "az": str(i.get("AvailabilityZone") or ""),
                            "status": str(i.get("DBInstanceStatus") or ""),
                            "cluster_id": cid,
                        }
                    )
            except Exception as exc:  # noqa: BLE001
                _warn(f"rds instances unexpected failure ({exc}); skipping")

        # ============== 18. ElastiCache =======================================
        if "elasticache" in services_set:
            try:
                ec = session.client("elasticache")
                for c in _paginate(ec, "describe_cache_clusters", "CacheClusters"):
                    if not isinstance(c, dict):
                        continue
                    cid = str(c.get("CacheClusterId") or "")
                    if not cid:
                        continue
                    sg = c.get("CacheSubnetGroupName") or ""
                    _emit_node(
                        {
                            "id": f"aws:elasticache:{cid}",
                            "type": "aws_elasticache",
                            "label": cid,
                            "cluster_id": cid,
                            "engine": str(c.get("Engine") or ""),
                            "engine_version": str(c.get("EngineVersion") or ""),
                            "node_type": str(c.get("CacheNodeType") or ""),
                            "subnet_group": str(sg),
                            "status": str(c.get("CacheClusterStatus") or ""),
                        }
                    )
            except Exception as exc:  # noqa: BLE001
                _warn(f"elasticache unexpected failure ({exc}); skipping")

        # ============== 19. ECR Repositories ==================================
        if "ecr" in services_set:
            try:
                ecr = session.client("ecr")
                for r in _paginate(ecr, "describe_repositories", "repositories"):
                    if not isinstance(r, dict):
                        continue
                    name = str(r.get("repositoryName") or "")
                    if not name:
                        continue
                    rid = f"aws:ecr_repo:{account_id}/{name}"
                    scan_cfg = r.get("imageScanningConfiguration") or {}
                    _emit_node(
                        {
                            "id": rid,
                            "type": "aws_ecr_repo",
                            "label": name,
                            "repo_name": name,
                            "uri": str(r.get("repositoryUri") or ""),
                            "scan_on_push": bool(scan_cfg.get("scanOnPush")),
                            "immutable_tags": str(r.get("imageTagMutability") or "")
                            == "IMMUTABLE",
                        }
                    )
            except Exception as exc:  # noqa: BLE001
                _warn(f"ecr unexpected failure ({exc}); skipping")

        # ============== 20. Lambda ============================================
        if "lambda" in services_set:
            try:
                lam = session.client("lambda")
                for f in _paginate(lam, "list_functions", "Functions"):
                    if not isinstance(f, dict):
                        continue
                    name = str(f.get("FunctionName") or "")
                    if not name:
                        continue
                    role_arn = str(f.get("Role") or "")
                    vpc = f.get("VpcConfig") or {}
                    _emit_node(
                        {
                            "id": f"aws:lambda:{name}",
                            "type": "aws_lambda",
                            "label": name,
                            "function_name": name,
                            "runtime": str(f.get("Runtime") or ""),
                            "handler": str(f.get("Handler") or ""),
                            "memory_mb": int(f.get("MemorySize") or 0),
                            "timeout_s": int(f.get("Timeout") or 0),
                            "role_arn": role_arn,
                            "vpc_id": str(vpc.get("VpcId") or ""),
                            "subnet_ids": list(vpc.get("SubnetIds") or []),
                        }
                    )
                    if role_arn:
                        _emit_edge(f"aws:lambda:{name}", f"aws:iam_role:{role_arn}", "executes_as")
            except Exception as exc:  # noqa: BLE001
                _warn(f"lambda unexpected failure ({exc}); skipping")

        # ============== 21. ALB / NLB =========================================
        if "lb" in services_set:
            try:
                elb = session.client("elbv2")
                for lb in _paginate(elb, "describe_load_balancers", "LoadBalancers"):
                    if not isinstance(lb, dict):
                        continue
                    arn = str(lb.get("LoadBalancerArn") or "")
                    if not arn:
                        continue
                    vpc_id = str(lb.get("VpcId") or "")
                    subnets = [
                        str(z.get("SubnetId") or "")
                        for z in lb.get("AvailabilityZones") or []
                        if isinstance(z, dict)
                    ]
                    subnets = [s for s in subnets if s]
                    sg_ids = [s for s in (lb.get("SecurityGroups") or []) if isinstance(s, str)]
                    _emit_node(
                        {
                            "id": f"aws:lb:{arn}",
                            "type": "aws_lb",
                            "label": str(lb.get("LoadBalancerName") or arn),
                            "arn": arn,
                            "lb_name": str(lb.get("LoadBalancerName") or ""),
                            "lb_type": str(lb.get("Type") or ""),
                            "scheme": str(lb.get("Scheme") or ""),
                            "vpc_id": vpc_id,
                            "subnets": subnets,
                            "security_groups": sg_ids,
                            "dns_name": str(lb.get("DNSName") or ""),
                        }
                    )
                    if vpc_id:
                        _emit_edge(f"aws:lb:{arn}", f"aws:vpc:{vpc_id}", "in_vpc")
                    for s in subnets:
                        _emit_edge(f"aws:lb:{arn}", f"aws:subnet:{s}", "uses_subnet")
                    for sg in sg_ids:
                        _emit_edge(f"aws:lb:{arn}", f"aws:sg:{sg}", "uses_sg")
            except Exception as exc:  # noqa: BLE001
                _warn(f"elbv2 unexpected failure ({exc}); skipping")

        # ============== 22. CloudFront ========================================
        if "cloudfront" in services_set:
            try:
                cf = session.client("cloudfront")
                # CloudFront paginator returns DistributionList.Items
                seen = 0
                try:
                    for page in cf.get_paginator("list_distributions").paginate():
                        items = (page.get("DistributionList") or {}).get("Items") or []
                        for d in items:
                            if not isinstance(d, dict):
                                continue
                            did = str(d.get("Id") or "")
                            if not did:
                                continue
                            origins = []
                            for o in (d.get("Origins") or {}).get("Items") or []:
                                if isinstance(o, dict):
                                    origins.append(str(o.get("DomainName") or ""))
                            _emit_node(
                                {
                                    "id": f"aws:cloudfront:{did}",
                                    "type": "aws_cloudfront",
                                    "label": did,
                                    "distribution_id": did,
                                    "domain_name": str(d.get("DomainName") or ""),
                                    "origins": [o for o in origins if o],
                                    "status": str(d.get("Status") or ""),
                                    "enabled": bool(d.get("Enabled")),
                                }
                            )
                            seen += 1
                            if seen >= per_limit:
                                break
                        if seen >= per_limit:
                            break
                except (NoCredentialsError, BotoCoreError, ClientError) as exc:
                    _warn(f"cloudfront.list_distributions failed ({exc}); skipping")
            except Exception as exc:  # noqa: BLE001
                _warn(f"cloudfront unexpected failure ({exc}); skipping")

        # ============== 23. Route53 hosted zones ==============================
        if "r53" in services_set:
            try:
                r53 = session.client("route53")
                for z in _paginate(r53, "list_hosted_zones", "HostedZones"):
                    if not isinstance(z, dict):
                        continue
                    raw_id = str(z.get("Id") or "")
                    zid = raw_id.split("/")[-1] if raw_id else ""
                    if not zid:
                        continue
                    cfg = z.get("Config") or {}
                    _emit_node(
                        {
                            "id": f"aws:r53_zone:{zid}",
                            "type": "aws_r53_zone",
                            "label": str(z.get("Name") or zid),
                            "zone_id": zid,
                            "name": str(z.get("Name") or ""),
                            "private": bool(cfg.get("PrivateZone")),
                            "record_count": int(z.get("ResourceRecordSetCount") or 0),
                        }
                    )
            except Exception as exc:  # noqa: BLE001
                _warn(f"route53 unexpected failure ({exc}); skipping")

        # ============== 24. ACM certificates ==================================
        if "acm" in services_set:
            try:
                acm = session.client("acm")
                for c in _paginate(acm, "list_certificates", "CertificateSummaryList"):
                    if not isinstance(c, dict):
                        continue
                    arn = str(c.get("CertificateArn") or "")
                    if not arn:
                        continue
                    _emit_node(
                        {
                            "id": f"aws:acm:{arn}",
                            "type": "aws_acm",
                            "label": str(c.get("DomainName") or arn),
                            "arn": arn,
                            "domain": str(c.get("DomainName") or ""),
                            "status": str(c.get("Status") or ""),
                            "cert_type": str(c.get("Type") or ""),
                            "in_use": bool(c.get("InUse")),
                        }
                    )
            except Exception as exc:  # noqa: BLE001
                _warn(f"acm unexpected failure ({exc}); skipping")

        # ============== 25. CloudWatch Log Groups =============================
        if "cw_logs" in services_set:
            try:
                logs = session.client("logs")
                groups = _paginate(logs, "describe_log_groups", "logGroups")
                for g in groups:
                    if not isinstance(g, dict):
                        continue
                    name = str(g.get("logGroupName") or "")
                    if not name:
                        continue
                    _emit_node(
                        {
                            "id": f"aws:cw_log_group:{name}",
                            "type": "aws_cw_log_group",
                            "label": name,
                            "log_group_name": name,
                            "retention_days": int(g.get("retentionInDays") or 0),
                            "kms_key": str(g.get("kmsKeyId") or ""),
                            "stored_bytes": int(g.get("storedBytes") or 0),
                        }
                    )
            except Exception as exc:  # noqa: BLE001
                _warn(f"cw logs unexpected failure ({exc}); skipping")

        _log(verbose, f"emitted {len(nodes)} nodes / {len(edges)} edges (region={region}, account={account_id})")
        return nodes, edges
