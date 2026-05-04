---
name: irsa-workload-identity
description: >-
  Bind Kubernetes ServiceAccounts to cloud IAM principals (IRSA on EKS, Workload Identity
  on GKE, AAD Workload Identity on AKS): trust policy, ServiceAccount annotations, and
  the verification commands that catch the four most common breakages.
---

# IRSA / Workload Identity

Use this skill when a pod needs to call a cloud API (S3, SecretsManager, SQS, GCS, KeyVault, …) **without** static credentials. The pattern is the same on every cloud: a Kubernetes **ServiceAccount** is exchanged at runtime for a short-lived cloud token via the cluster's **OIDC provider**.

## The chain — get every link right

| Link | EKS (IRSA) | GKE (Workload Identity) | AKS (AAD WI) |
|------|------------|------------------------|--------------|
| Cluster OIDC issuer registered with cloud IAM | ✅ one-time (`aws iam create-open-id-connect-provider`) | ✅ enabled at cluster create | ✅ `--enable-oidc-issuer --enable-workload-identity` |
| Cloud IAM principal trusts the OIDC issuer + SA subject | IAM Role trust policy | IAM Service Account `roles/iam.workloadIdentityUser` | Federated identity credential |
| K8s ServiceAccount annotated with the principal | `eks.amazonaws.com/role-arn` | `iam.gke.io/gcp-service-account` | `azure.workload.identity/client-id` |
| Pod uses that ServiceAccount and (AKS) is labeled | `serviceAccountName` | `serviceAccountName` | `serviceAccountName` + `azure.workload.identity/use: "true"` |

If **any** link is wrong, the pod still runs but API calls return `AccessDenied` / `Permission denied`. This skill exists to make you check all four.

## EKS / IRSA — minimum viable setup

### 1. IAM role trust policy

The trust policy must reference the cluster's OIDC issuer **and** scope to a specific namespace/SA. Wildcard subjects are a foot-gun.

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": { "Federated": "arn:aws:iam::<account>:oidc-provider/oidc.eks.<region>.amazonaws.com/id/<oidc-id>" },
    "Action": "sts:AssumeRoleWithWebIdentity",
    "Condition": {
      "StringEquals": {
        "oidc.eks.<region>.amazonaws.com/id/<oidc-id>:sub": "system:serviceaccount:<namespace>:<sa-name>",
        "oidc.eks.<region>.amazonaws.com/id/<oidc-id>:aud": "sts.amazonaws.com"
      }
    }
  }]
}
```

Use **`StringEquals`**, not `StringLike` with `*`. If you need multiple SAs, list them under a `ForAnyValue:StringEquals` of `:sub` — don't open the prefix.

### 2. ServiceAccount annotation

```yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  name: app-sa
  namespace: payments
  annotations:
    eks.amazonaws.com/role-arn: arn:aws:iam::<account>:role/payments-app
```

### 3. Pod spec

```yaml
spec:
  serviceAccountName: app-sa
  # Tokens auto-projected by the EKS pod identity webhook; no env vars needed.
```

## GKE / Workload Identity

```bash
# Bind GCP SA to K8s SA
gcloud iam service-accounts add-iam-policy-binding \
  payments@<project>.iam.gserviceaccount.com \
  --role roles/iam.workloadIdentityUser \
  --member "serviceAccount:<project>.svc.id.goog[payments/app-sa]"
```

```yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  name: app-sa
  namespace: payments
  annotations:
    iam.gke.io/gcp-service-account: payments@<project>.iam.gserviceaccount.com
```

## AKS / AAD Workload Identity

The pod **must** carry `azure.workload.identity/use: "true"` as a **label** (not annotation). Forgetting it is the #1 cause of "it doesn't work" tickets.

```yaml
spec:
  template:
    metadata:
      labels:
        azure.workload.identity/use: "true"
    spec:
      serviceAccountName: app-sa
```

## Verify before you debug

Run these in order — the first that fails localizes the broken link.

```bash
# 1. SA exists and is annotated
kubectl -n <ns> get sa <name> -o yaml | grep -A2 annotations

# 2. Pod uses the SA and projected token is mounted
kubectl -n <ns> exec <pod> -- ls /var/run/secrets/eks.amazonaws.com/serviceaccount   # EKS
# GKE: /var/run/secrets/tokens/   AKS: /var/run/secrets/azure/tokens/

# 3. Token is exchangeable — call the cloud's "who am I"
kubectl -n <ns> exec <pod> -- aws sts get-caller-identity                            # EKS
kubectl -n <ns> exec <pod> -- gcloud auth list                                       # GKE
kubectl -n <ns> exec <pod> -- az account show                                        # AKS

# 4. Actual API call (least privilege you expect to work)
kubectl -n <ns> exec <pod> -- aws s3 ls s3://<bucket>/
```

If `get-caller-identity` returns the **node** role, the IRSA webhook isn't injecting — most often the SA isn't annotated **at the time the pod started**. Restart the pod after fixing the SA.

## The four common breakages

1. **Wrong OIDC issuer** in the trust policy (typo, or stale after cluster recreate). `aws eks describe-cluster --name <c> --query cluster.identity.oidc.issuer` is authoritative.
2. **Wildcard `:sub`** in trust policy → any SA in the cluster can assume the role. Tighten to a specific `system:serviceaccount:<ns>:<name>`.
3. **Pod predates SA annotation.** The token is injected at admission; existing pods keep the old (node) identity until restarted.
4. **AKS missing the `use: "true"` label** on the **pod template**, not the SA. The annotation goes on the SA; the label goes on the pod.

## Pair with

- **`application-env-and-secrets`** — when you migrate static creds to IRSA, scrub the env vars / Secrets you no longer need.
- **`troubleshooting-aws-observability`** — `AccessDenied` triage path through CloudTrail.
- **`secrets-rotation-lifecycle`** — IRSA removes most rotation work; document what's left.

## kuberly-stack notes

Cluster IAM trust roots are in `components/<cluster>/shared-infra.json`; per-app role ARNs go on the application JSON consumed by CUE. Don't hardcode role ARNs into Helm values — read them from the generated K8s manifest so cluster moves stay safe.
