# RAG index hints: kuberly-stack-shaped monorepos

Use this note when designing **retrieval**, **MCP search**, or **embedding pipelines** for an infra repo that mirrors **kuberly-stack** (Terragrunt + JSON under `components/` and `applications/`).

## Labels the retriever should surface

Attach metadata to each chunk so the model can route queries:

- `tree:components` | `tree:applications` | `tree:clouds-modules` | `tree:openspec` | `tree:docs-root`
- `artifact:json-schema` | `artifact:how-to` | `artifact:example` | `artifact:hcl` (HCL last — large and noisy)

## Query routing examples

| User intent | Likely best `tree` |
|-------------|-------------------|
| “Bump RDS for prod” | `components` + `clouds-modules` |
| “Add env var to api on ECS dev” | `applications` |
| “Why does plan touch both EKS and my app?” | `components` + `applications` + link to dependency docs |
| “OpenSpec for this change” | `openspec` |

## Chunking

- **Docs:** one chunk per markdown section (## heading).
- **Large JSON:** chunk by **top-level key**; include the **filename stem** in metadata (`application_name: backend`).
- **HCL modules:** prefer **README + variables.tf description** over full resource blocks until volume forces full indexing.

## Refresh

- Re-index on **tag** or **main merge**, not every commit, unless the pipeline is cheap.
- Invalidate chunks whose **source path** changed in the diff touching `components/`, `applications/`, or `clouds/*/modules/<name>/`.

## Tenancy

If customers each have a **fork**, indexes should be **per fork** (or per tenant namespace) so retrieval never mixes org A’s ARNs with org B’s.
