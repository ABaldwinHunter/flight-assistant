# Flight assistant engineering rules

- Keep this an explainable tool-calling demo: Python/FastAPI, SQLite, vanilla browser client, optional CLI. No agent framework, RAG, booking, or frontend build stack.
- Use a real LLM API. Never silently substitute a fake model. Keep API keys server-side, out of logs, responses, traces, and version control.
- Label all flight inventory, prices, and availability **simulated**. Do not describe a sandbox provider as live inventory.
- Flight claims must come from successful allowlisted tool results. Ask for missing route/date details; distinguish empty results from tool failures.
- Bound model calls, tool calls, output size, and retained conversation context. Keep the orchestration loop explicit and readable.
- Persist conversations, messages, and observable tool arguments/results/errors. Do not request or store private model reasoning.
- Validate tool arguments independently of the model's schema. Parameterize SQL. Treat user/model/tool text as untrusted; render it as text in the browser.
- Preserve conversation isolation and grounded follow-ups across process restarts. Failed/interrupted turns must not corrupt the next provider request.
- Exercise changed behavior end to end. Keep permanent tests for consumer-visible boundaries and failure recovery, not implementation wording.
- Update the README when commands, settings, limits, or behavior change. Keep this a local-only demo unless authentication and deployment protections are explicitly added.
