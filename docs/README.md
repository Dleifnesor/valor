# VALOR documentation

| If you want to... | Read |
|---|---|
| Install VALOR on a Proxmox VE cluster, upgrade or remove it | [INSTALL.md](INSTALL.md) |
| Use the web UI: build ranges, change them, get into them, administer VALOR | [USER-GUIDE.md](USER-GUIDE.md) |
| Write or read a range spec (the YAML format) | [spec reference](../.claude/skills/range-build/spec-reference.md) |
| Look up the services, hardening baselines and compliance frameworks VALOR ships | [CONTENT.md](CONTENT.md) |
| Use the command line (`install.sh`, `valor`, `valor-web`, `valor-admin`) | [CLI.md](CLI.md) |
| Understand how the code is put together | [ARCHITECTURE.md](ARCHITECTURE.md) |
| Call the web API | [API.md](API.md) |
| Work on VALOR: set up, test, change roles/baselines/OSes, ship | [DEVELOPMENT.md](DEVELOPMENT.md) |
| See what is done and what comes next | [ROADMAP.md](../ROADMAP.md) |

The [README](../README.md) is the one-page overview: what VALOR is, how a build works and the security model.

## Words used in these documents

| Word | Meaning |
|---|---|
| **Range** | One test environment: a router, one or more network segments and the hosts (VMs) on them. |
| **Spec** | The YAML file that describes a range. It is the only input the engine builds from. |
| **Segment** | One network inside a range: a VLAN on the range bridge with its own private IPv4 network. |
| **Router** (`rtr`) | The VM VALOR adds to every range. It routes between the segments, enforces the traffic policy (nftables) and gives segments marked `internet: true` NAT access to the internet. |
| **Policy** | The traffic allowed between segments or hosts. Everything not listed is blocked. |
| **Role** | A service VALOR can install on a host, such as `nginx`, `postgresql` or `ad-dc`: an idempotent script plus its metadata. |
| **Baseline** | A hardening profile: controls with a check and a fix, applied during the build and checked again during verification. |
| **Plan** | What a build would do to each VM (create, update, replace, converge, keep, remove...). A person approves a plan before it runs. |
| **Draft** | Changes to a built range that are not built yet (made on the map or by the range chat). A draft is built through plan -> approve. |
| **Job** | A background task that changes the cluster: a build, verification, teardown, power action or snapshot. |
| **Blueprint** | A spec kept as a pattern, deployed as any number of identical copies (one per student, for example). |
| **Engine** | The Python package (`valor/`) that validates, plans, builds, verifies and destroys ranges. It is the only part of VALOR that calls the Proxmox API. |
| **VALOR VM** | The VM the installer creates. It runs the web UI, the web API, the job worker and the engine. |
