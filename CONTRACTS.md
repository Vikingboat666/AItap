# CONTRACTS

These four files are **shared interfaces** that many parallel worktrees depend on. Changes need extra care.

| File | What it defines | Downstream consumers |
|---|---|---|
| `src/aitap/scanner/models.py` | `PromptSite`, `Pipeline`, `ScanResult`, `Edge`, enums | `scanner/*`, `store/*`, `audit/*`, `playground/*`, `dataset/*`, `iterate/*`, `server/routes/*`, `ui/*` |
| `src/aitap/store/db.py` (schema only) | SQLite tables, columns, types | `playground/*`, `iterate/*`, `server/routes/*`, `cli.py` |
| `src/aitap/server/routes/__init__.py` | OpenAPI request/response pydantic models | `server/routes/*` (backend), `ui/*` (frontend, via openapi-typescript-codegen) |
| `src/aitap/deep/client.py` | `LLMClient` ABC, `ChatMessage`, `ChatResponse`, `CostEstimate` | `deep/*`, `dataset/llm_expander.py`, `iterate/judge.py`, `iterate/critic.py`, providers in `deep/` |

## Change protocol

1. **Open an issue** describing the contract change and the breaking impact.
2. **Single PR per contract change.** Don't bundle contract edits with feature work.
3. **Tag all worktree owners** in the PR — they need to rebase and adapt.
4. **Pin a version comment** at the top of the contract file when shipping a breaking change:
   ```python
   # Contract version: 2 (2026-MM-DD) — breaking change: Pipeline.edges renamed to Pipeline.connections
   ```
5. **TS regeneration**: after merging an OpenAPI change, regenerate `src/aitap/ui/src/api/types.ts`:
   ```bash
   pnpm --dir src/aitap/ui run gen:api
   ```

## Owners (initial)

- `scanner/models.py` — TBD
- `store/db.py` — TBD
- `server/routes/__init__.py` — TBD
- `deep/client.py` — TBD

Update when ownership is assigned.

## Avoiding contract drift

- The Wave 0 / contract anchor authors should write **example consumer snippets** in module docstrings so downstream worktrees have a stable reference.
- Contract files should have **>90% docstring coverage** — they're read more than written.
- Adding **new** types/fields is non-breaking; renaming, removing, or retyping is breaking.
