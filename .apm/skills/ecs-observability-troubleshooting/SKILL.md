---
name: ecs-observability-troubleshooting
description: >-
  Troubleshoot ECS services on Kuberly stacks: CloudWatch Logs and metrics, ECS events, CloudTrail,
  ALB / Service Connect, and correlation with VPC.
---

# ECS — troubleshooting playbook

Use when **`shared-infra`** + **`ecs.json`** (or deployed ECS modules) show **ECS** is the primary compute — not EKS.

## Logs

- **CloudWatch log groups** for the task definition family / `awslogs` driver / FireLens destinations.
- Filter by **known task ARN** or **container exit code** around the incident window.

## Service health

- **Amazon ECS → Cluster → Service → Events**: placement failures, capacity, image pull errors, ELB registration.
- **Metrics**: `CPUUtilization`, `MemoryUtilization`, running/pending/desired counts; **ALB** `HealthyHostCount` / `TargetResponseTime`.

## API / access audit

- **CloudTrail**: failed `ecs:*`, `elasticloadbalancing:*`, `ec2:AuthorizeSecurityGroupIngress` tied to the same minute as the outage.

## Networking

- Tasks in **private subnets** need **NAT** or **VPC endpoints** for ECR / Secrets Manager.
- **Security groups**: service ↔ load balancer ↔ dependency ports.

## Exec (optional)

- **`aws ecs execute-command`** when enabled on service/task def — use only with customer approval and break-glass process.
