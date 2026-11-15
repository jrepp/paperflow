# Paperflow deployment

Example rootless Podman Quadlet for a local Paperflow workspace. The container
stores mutable state under `/data`, backed by `/srv/paperflow` on the host.
The example exposes port 8410 on loopback only.

## Build and install

From the repository root:

```bash
podman build -f Containerfile -t localhost/paperflow:local .
mkdir -p ~/.config/containers/systemd
cp deploy/paperflow.container ~/.config/containers/systemd/paperflow.container
systemctl --user daemon-reload
systemctl --user start paperflow.service
```

Before starting, create a writable `/srv/paperflow` volume and place the runtime
configuration at `/srv/paperflow/arxiv-radar.yaml`. Adapt the volume path to your
host. Keep environment-specific addresses and credentials outside the repository.
Remote access requires your own authenticated gateway or private network setup.

## Health

```bash
systemctl --user status paperflow.service
curl http://127.0.0.1:8410/
```

## External deployment settings

Treat the Quadlet as a loopback-only example. Operators own interface bindings,
firewall rules, gateway routes, DNS, TLS, and identity-provider configuration in
an external deployment repository. Paperflow neither discovers nor configures
those services. The container's port and volume mapping belong to the installed
unit; edit that external unit rather than committing a host-specific copy here.

Static publishing omits the workspace link by default. To include one, supply an
external settings file via `--config`:

```yaml
research:
  publishing:
    workspace_url: https://reader.example.com/workspace
```

Alternatively set `PAPERFLOW_PUBLISHING__WORKSPACE_URL` or pass
`paperflow publish site --workspace-url https://reader.example.com/workspace`.
Precedence is CLI override, environment, YAML, then no link. An empty value
explicitly disables the link. HTTP(S) URLs and root-relative paths are supported;
credentials, executable URL schemes, and protocol-relative URLs are rejected.
The URL is included in the public HTML, so do not supply a private endpoint or
credentials unless that endpoint is intentionally public information.
