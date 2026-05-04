## Problem

<!-- What is broken or missing in the customer infra / fork? -->

## Solution

<!-- Modules, components, applications, OpenSpec path. -->

## OpenSpec

<!-- If required: `OpenSpec: openspec/changes/...` or archive path. -->

## Testing

- [ ] `pre-commit` (including APM sync if `apm.yml` lists deps)
- [ ] `terragrunt run plan` on: <!-- module(s) --> — summary + fenced plan excerpt

## Risks

- **Blast radius:**
- **Rollback:**
- **Out of band:**

## Flow (optional Mermaid)

<!-- Use subgraph ids without spaces; avoid `end` as a node id. See skill `infra-change-git-pr-workflow`. -->

```mermaid
flowchart LR
  subgraph prep [Prep]
    Fetch[git fetch]
    Base[checkout integration]
    Branch[feature branch]
  end
  subgraph chg [Change]
    Code[edit IaC]
    Plan[terragrunt plan]
  end
  subgraph ship [Ship]
    PR[PR to integration]
  end
  Fetch --> Base --> Branch --> Code --> Plan --> PR
```
