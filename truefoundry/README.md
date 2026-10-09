# TrueFoundry MCP Gateway desired state

This directory contains the production gateway configuration for Moodle MCP.

- `manifests/` contains secret-free, directly applicable `tfy apply` manifests. The files use JSON
  syntax, which is valid YAML.
- `policies/moodle-access.cedar` is the reviewable source for the pre-tool authorization policy;
  `manifests/41-moodle-access-guardrail-group.yaml` provisions that policy as
  `moodle-access/cedar-rbac`.
- `../scripts/validate_truefoundry_gateway.py` proves the manifests remain aligned with the source
  tool inventory, student allowlist and approved staff roster.
- `manifests/40-gateway-data-access.yaml` removes the default teammate-trace exposure: people can
  inspect their own traces and shared aggregate metrics, while full trace access stays admin-only.
- `manifests/41` through `43` provision the Cedar, PII, secret-detection and prompt-injection
  guardrail groups in enforced mode.
- `manifests/50-moodle-guardrails.yaml` binds Cedar before every Moodle tool call and PII, secrets
  and indirect-prompt-injection checks after every result.

Preview before changing the tenant:

```bash
python3 scripts/validate_truefoundry_gateway.py
tfy apply --dir truefoundry/manifests --dry-run --show-diff
```

Apply only from an authenticated tenant-admin workstation after the dry-run and staging canary are
clean:

```bash
tfy apply --dir truefoundry/manifests
```

Before enforcing the private-origin boundary, store a high-entropy value in the TrueFoundry secret
store and send it as `x-mcp-gateway-secret`; configure the same value as the Cloudflare
`GATEWAY_SHARED_SECRET`. Never place the value in a manifest, shell history or Git. No TrueFoundry
token, upstream OAuth token, provider credential or notification secret belongs in these files.
