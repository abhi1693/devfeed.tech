# Partner API

An independently deployable, OIDC-authenticated service for partner memberships and reporting.
See [portal architecture, setup, authorization, and rollout](../partner/README.md).
The service owns only `/v1/partner` and health routes and listens on port 8004. The public
API and reader sessions cannot access partner data. Its Docker image installs only this
service and its core/HTTP dependencies.
