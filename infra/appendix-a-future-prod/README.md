# Appendix A — Future Production Design (documentation only)

Nothing in this folder is wired into CI or meant to run. It documents the full
production target described in docs/PROJECT_PLAN.md Appendix A:

- Self-hosted HA PostgreSQL + TimescaleDB (primary + replica, PgBouncer, failover)
- Full 12+ service Kubernetes namespace with load-test-tuned HPA
- Per-tenant MinIO bucket isolation with lifecycle policies
- OAuth2/SSO (Google/Microsoft) enterprise identity federation
- Multi-region deployment considerations
- Real IoT integration (OPC-UA, MQTT broker clustering)

Add design docs / example (non-functional) manifests here as you flesh this out.
