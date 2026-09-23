"""Enforcement backends: turn a manifest's capability declarations into
real, OS/container-level mechanisms, not Python code inspecting every
open() or exec().

- filesystem.py -- computes a container mount plan (bind mounts + tmpfs
  masks) from `capabilities.filesystem`.
- process.py -- computes a PATH-restricted binary allowlist from
  `capabilities.process`.

Network enforcement isn't here: idhn/network/ (proxy.py, policy.py,
callers.py) already fills this role for network -- it's a full gateway
service, not a small policy-to-mechanism function, so it stays where it
is rather than being duplicated or re-exported here for cosmetic
symmetry.

Each module here only describes WHAT needs enforcing, as backend-neutral
data (MountSpec, a list of binary names) -- idhn/runtime/docker.py is
what translates that into actual Docker Compose YAML. Keep it that way:
a later runtime backend (gVisor, Firecracker, ...) should be able to
consume these same descriptions without this package knowing Docker
exists.
"""
