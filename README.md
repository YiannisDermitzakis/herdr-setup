# herdr-setup

Reproduce a [Herdr](https://herdr.dev) setup on another machine, and keep several
machines in step.

Herdr has no export or sync of its own. This is a small, dependency-light tool that
copies a herdr configuration out of a host into a git checkout, and back into another
host. It also onboards the coding agents on a host so that a server restart resumes
their sessions instead of stranding them.

Status: under construction. See `docs/` once the first release lands.
