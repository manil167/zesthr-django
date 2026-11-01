# ADR 0001: Modular monolith with domain ownership

- Status: Accepted
- Date: 2026-10-25

## Context

HROS currently delivers attendance, leave, payroll and employee operations from
one Django application, `core`. The product roadmap expands this into a global
HRMS with strong tenant isolation, sensitive data controls and many connected
workflows. A large rewrite or immediate microservice split would risk the
working system and make the product harder to operate.

## Decision

HROS will evolve as a modular Django monolith. New domains are Django apps
under `apps/`, and each domain owns its models, migrations, services, selectors
and permissions. `core` remains a temporary compatibility facade while existing
features are incrementally extracted.

Cross-domain writes use a published service interface or a domain event. Views,
templates and API controllers do not contain business rules. Organization scope
is mandatory for multi-tenant business data, and sensitive reads require both
tenant scope and role-based authorization.

## Consequences

This approach retains one deployment, transaction boundary and development
workflow while giving the codebase clear ownership boundaries. It makes future
extraction possible without promising it prematurely. During the transition,
some adapter code and duplicated tests are expected; those adapters must be
removed only after all consumers move to the owning domain.
