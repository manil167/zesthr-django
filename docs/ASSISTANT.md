# HROS Assistant

The signed-in chat widget answers conversational HROS workflow questions using the OpenAI Responses API. It supports follow-up messages, a bounded session history, new conversations, suggested questions, keyboard controls, and explicit loading/error states. Missing credentials or an intentionally disabled assistant select clearly labelled deterministic app guidance; an upstream failure never pretends a keyword response came from AI.

## Configuration

Store OPENAI_API_KEY privately in the application runtime secret settings. Set DJANGO_ASSISTANT_ENABLED=True to enable AI explicitly, DJANGO_ASSISTANT_MODEL (default gpt-6-luna), and per-user DJANGO_ASSISTANT_MINUTE_LIMIT / DJANGO_ASSISTANT_DAILY_LIMIT (10 / 100 by default). Apply migrations before starting the new release; migration 0035 adds usage counters. No new Python dependency is required.

A local key in .env.local does not configure Render. That file is ignored by Git and excluded from Docker. Production activation requires the key to be configured separately through the hosting secret manager. Never place it in templates, JavaScript, logs, chat, or committed configuration.

## Boundaries and retention

Only signed-in users can access GET/POST/DELETE /help/chat/. Paid AI additionally requires active organization membership or an active platform administrator; unassigned public signups receive app guidance. Unsafe methods retain CSRF protection. The assistant cannot query HR records, access documents, run tools, approve leave, modify attendance, or decide employment outcomes. Only the user's chat text, bounded server-owned history, and curated non-personal app guidance reach the provider. Company-specific policy and individual records require authorized portal/HR access. Users are told about external processing and should omit confidential details.

History is held in the existing server-side Django session, not browser localStorage. At most 12 messages are retained, with at most eight preceding messages sent for a follow-up. New conversation deletes that history from the active session. Logout clears the session; normal session-expiration cleanup still applies. Usage counters contain no prompts, answers, email, or provider secrets. They are user-scoped and independent of session resets. Calendar usage days use UTC.

Provider requests set store=false. This disables Responses persistence but is not a promise of zero retention or a substitute for reviewing OpenAI's data controls. No internal reasoning or raw provider response is exposed. The server uses finite connection/read timeouts and does not automatically retry billed requests. Client rendering uses text/DOM nodes rather than innerHTML, and accepts only local navigation links from the deterministic guide.

Usage checks serialize on the user row in a short transaction, across PostgreSQL web workers. They complete before the external request; failed upstream attempts still consume the usage budget to bound retry abuse. Limits are not a financial spending cap: configure the provider project's budget/alerts separately. The synchronous endpoint is bounded but may need a dedicated queue or asynchronous service if measured chat volume exceeds web-worker capacity.

## Verification

Run python manage.py test core.tests.test_assistant_chat. Tests cover CSRF, authentication, history isolation and bounds, client-supplied history rejection, usage windows and resets, failure handling, production privacy boundaries, and PostgreSQL concurrency. A live synthetic prompt verifies provider/model access without sending employee data. The ordinary test settings disable all live AI requests.

The assistant provides app guidance, not a legal, medical, tax or HR decision service. It can be wrong; approvals and financial/attendance changes continue through the existing audited workflows.
