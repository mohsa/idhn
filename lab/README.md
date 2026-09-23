# lab/

Disposable environment for exercising IDHN. Nothing under here should
ever be mounted with real credentials or point at a real host filesystem
path.

- `repos/toy-app` -- the toy Node.js project IDHN's M0 run fixes. Mounted
  read-write at `/workspace` in the agent container. Nothing else is.
- `fake-secrets/` -- credential-shaped files that are safe to leak, used
  in later benchmark attack scenarios to check whether IDHN detects
  attempted access to paths it was told to deny.
- `services/` -- placeholder internal services for M2 (transitive
  capability escape) testing.
