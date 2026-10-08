# TrueFoundry MCP Gateway desired state

This directory contains the production gateway configuration for Moodle MCP.

- `manifests/` contains secret-free, directly applicable `tfy apply` manifests. The files use JSON
  syntax, which is valid YAML.
- `policies/moodle-access.cedar` is the pre-tool authorization policy to paste into a Cedar
  guardrail and bind to `jaipuria-moodle-prod`.
- `../scripts/validate_truefoundry_gateway.py` proves the manifests remain aligned with the source
  tool inventory, student allowlist and approved staff roster.

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

No TrueFoundry token, upstream OAuth token, provider credential or notification secret belongs in
these files.
