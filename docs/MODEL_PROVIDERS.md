# Model routes

Provider keys are stored either in environment variables or encrypted connector records. The
phone's Connectors page configures FreeGG, AgentRouter, and Xingchi. `/api/models` returns
configured coding/chat routes; `/api/automation/status` also includes generation-only free models.

| Model ID | Protocol | Configuration |
| --- | --- | --- |
| `auto/gpt-6.1-sol` | Configured GPT routes | Optional video series setting, available when at least one listed FreeGG, AgentRouter OpenAI, or Xingchi route is configured |
| `freegg/gpt-6.1-sol` | Responses | FreeGG connector or `FREEGG_API_KEY` |
| `agentrouter-openai/gpt-6.1-sol` | Responses | AgentRouter connector or `AGENTROUTER_OPENAI_API_KEY` |
| `agentrouter-openai-two/gpt-6.1-sol` | Responses | AgentRouter Two connector |
| `xingchi/gpt-6.1-sol` / `xingchi2/gpt-6.1-sol` | Responses, optional chat | Xingchi connectors or `XINGCHI_BASE_URL`, `XINGCHI_API_KEY` |
| `opencode/gpt-6.1-sol` | Responses | Explicit `OPENCODE_API_KEY`; not a free model |
| `opencode/*-free`, `opencode/big-pickle` | Actual OpenCode CLI | `OPENCODE_BIN`, generation-only |
| `local/qwen3:1.7b` | Loopback Ollama | `LOCAL_LLM_ENABLED=true`, generation-only |

Legacy native Gemini (`AGENTROUTER_API_KEY`) and b.ai keys are still supported. AgentRouter's
OpenAI key uses **separate** `AGENTROUTER_OPENAI_*` settings so it is never sent to Google.

New pinned provider routes have no unrelated automatic provider fallback. Video automatic mode
tries configured FreeGG, then OpenCode, then CPU inference. CPU fallback never calls Ollama Cloud.

In the Studio series editor, choose **GPT-6.1 Sol · FreeGG / AgentRouter / Xingchi failover**
to try those providers in order if a request fails or times out. The series skips unconfigured
routes and tries the secondary AgentRouter or Xingchi connector after its primary connector.
The configured accounts' quotas and rates apply. If all selected routes fail, the run reports
the attempted routes. Leave the Model field on **Automatic · FreeGG / OpenCode / CPU** for
the existing free workflow.

`scripts/import_model_providers.py --apply` imports requested providers from the user's own
OpenCode configuration into encrypted connectors. It does not modify OpenCode or print key values.
Provider keys do not enter the free OpenCode subprocess environment, renderer prompts, or previews.

Use `scripts/check_free_models.py --catalog` to check the isolated CLI catalog. A successful catalog
does not prove generation quota; use a tiny explicit request for that. The app recognizes provider
quota failures and times out bounded requests rather than waiting indefinitely.
