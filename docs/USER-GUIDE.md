# Using VALOR

This guide covers the web UI, from the first sign-in to building, changing, using and removing ranges. It assumes
VALOR is installed ([INSTALL.md](INSTALL.md)). Terms such as *range*, *segment*, *role* and *plan* are explained in
the [documentation index](README.md#words-used-in-these-documents).

- [Signing in](#signing-in)
- [Who can do what](#who-can-do-what)
- [Finding your way](#finding-your-way)
- [Building your first range](#building-your-first-range)
- [Plans and approval](#plans-and-approval)
- [The range page](#the-range-page)
- [Changing a built range](#changing-a-built-range)
- [The range chat](#the-range-chat)
- [When a build fails](#when-a-build-fails)
- [Getting into a range](#getting-into-a-range)
- [Power and snapshots](#power-and-snapshots)
- [Removing a range](#removing-a-range)
- [ISO library](#iso-library)
- [Blueprints: many copies of one range](#blueprints-many-copies-of-one-range)
- [Compliance frameworks](#compliance-frameworks)
- [Administration](#administration)
- [Troubleshooting](#troubleshooting)

## Signing in

Open `https://<valor-vm>/` from a network the installer allowed (the LAN and any VPN subnets you listed). If the
browser warns about the certificate, download `https://<valor-vm>/ca.crt` (also linked on the sign-in page) and
trust it once in your OS or browser.

1. **Password.** The first administrator's password is in `/root/valor-<id>-credentials.txt` on the Proxmox node
   where VALOR was installed. Accounts that an administrator creates or resets must choose a new password at their
   next sign-in.
2. **Two-factor authentication** is required for everyone. At the first sign-in VALOR shows a QR code: scan it with
   an authenticator app (any TOTP app) and enter a code. You then get **10 recovery codes**. Each works once in place
   of an authenticator code, so keep them somewhere safe.
3. Later sign-ins ask for the password and a current code.

Under your name (top right) -> **My account** you change your password and make new recovery codes. After 5 wrong passwords or codes in a
row, an account is locked for 15 minutes (an administrator can unlock it sooner). Sessions end after 30 minutes
without activity and after 12 hours at most.

If VALOR is connected to LDAP or Active Directory, you sign in with your directory password. Your role comes from
your directory groups, and two-factor authentication is still required.

## Who can do what

| Role | Can |
|---|---|
| **Viewer** | See the summary, ranges, maps, verification results, journals, jobs, snapshots, the ISO library, blueprints and their own chats. |
| **Operator** | Everything a viewer can, plus: write and save specs, plan and approve builds, verify, destroy, edit ranges on the map, use the chat builder and range chats, open VM consoles, see range logins and WireGuard configs, power and snapshots, add ISOs, manage blueprints. |
| **Admin** | Everything an operator can, plus: users, the audit log, settings (notifications, LDAP, AI providers), deleting ISOs, and reading every user's chats. |

Viewing a range login or a WireGuard peer config, opening a console and every change are recorded in the audit
log.

## Finding your way

The web UI is laid out like Proxmox VE's:

| Part | What it holds |
|---|---|
| **Header** | **Search** (ranges and VMs by name, VMID or description), **Create range**, **Chat builder**, and your name: **My account**, light / dark theme, sign out. The menu button at the left shows or hides the tree. |
| **Resource tree** (left) | VALOR itself (**Datacenter**), every range, and each built range's VMs as `VMID (host)`. A green mark means running. Click an entry to open it. |
| **Panel** (right) | The selected entry: its actions in the title bar (for a range: Start, Shutdown, Login, Verify, Plan, More), its sections on the left, the section on the right. |
| **Tasks** (bottom) | The latest jobs with their start and end time, range, user and status; click one for the task viewer (its steps, live). **Notifications** next to it holds VALOR's messages. The arrow at the right folds the list away. |

**Right-click menus.** As in Proxmox, right-clicking something opens a menu of what you can do to it:

| Right-click | Menu |
|---|---|
| **Datacenter** in the tree | Create range, Chat builder |
| A range (tree or the Summary's range list) | Start / shut down / reboot / force-stop all VMs, Login, Verify, Plan changes, Edit spec, Destroy (asks you to type the name, as always) |
| A VM (tree, a range's VM list, its box on the map) | Start, Shut down, Reboot, Force stop, Console (noVNC), Serial console (xterm.js) |
| A segment or VM on the **Map** | Details, plus the map's edits (add a VM or service, allow traffic, edit, remove) |
| A task, a chat | Open it, open its range, copy the task ID / delete the chat |

Entries that do not apply right now (Start on a running VM) are greyed out; viewers see only the entries that change nothing.
**Shift** + right-click opens the browser's own menu; the context-menu key (or Shift+F10) opens VALOR's menu on the
focused entry, and the arrow keys move through it.

The **Datacenter** sections: **Summary** (health, VMs, resources, every range, health checks), **Chats**,
**Blueprints**, **ISO library**, **Jobs**, and for administrators **Users**, **Audit log** and the settings
(**Directory (LDAP)**, **AI providers**, **Notifications**, **Certificate**).

## Building your first range

There are three ways to describe a range. All three end in the same place: a validated spec, a plan, and a build
that runs only after you approve it.

### With the chat builder

**Chat builder** (top right) -> describe the environment in plain words, for example:

> A DMZ with an nginx web server that can reach the internet, and an isolated LAN with PostgreSQL that only the web
> server can reach on 5432.

The assistant may ask questions first (shown as cards with options; pick one or type your own answer). It then
writes a spec. VALOR validates it against the schema and the cluster and sends any problems back to the model to fix
before you see it. The result is shown as a map with the spec next to it. Keep refining in the same conversation
("add a Kali attacker host in its own segment that can reach the DMZ"). When it looks right, use **Save as range** or
**Open in editor**.

The chat builder needs an AI provider. An administrator connects one under **Datacenter -> AI providers**.

### With the spec editor

**Create range** (top right) opens the editor. Write or paste a spec (format:
[spec reference](../valor/spec-reference.md); examples: [`examples/`](../examples/)). The side
panel lists what this cluster offers: operating systems, roles, baselines, VLANs in use and networks to avoid.
**Check** validates the spec and shows its map. **Save spec** keeps it.

### From a blueprint

See [Blueprints](#blueprints-many-copies-of-one-range).

### Build it

Select the range in the tree: **Plan build** shows the plan on the map and as a list. Read it, then **Approve and build**. The
build runs as a job: the task viewer opens and shows its steps live (it stays in **Tasks** at the bottom). A typical build:

1. creates the router and hosts as linked clones of the OS templates (about a minute each),
2. opens temporary internet access for package installs,
3. installs the roles (services) and applies the hardening baseline,
4. loads the final traffic policy, so the range ends up exactly as specified,
5. verifies: connectivity tests from inside the VMs, isolation and egress tests, and every baseline check on every
   host,
6. takes a snapshot named `valor-clean` if verification passed.

Results are in the range's **Verification** section. When something fails, see
[When a build fails](#when-a-build-fails).

## Plans and approval

Every build and every teardown shows its plan before it runs. Per VM the plan says:

| Action | Meaning |
|---|---|
| `create` | a new VM |
| `update` | CPU, memory or disk changed: resized, then rebooted |
| `replace` | OS, network or address changed: the VM is deleted and built again (its data is lost) |
| `converge` | roles, baseline or policy changed: applied in place, no reboot |
| `start` | the VM exists but is stopped |
| `restamp` | only VALOR's bookkeeping on the VM changes |
| `keep` | nothing to do |
| `remove` | the host is no longer in the spec: the VM is deleted |

The plan also shows memory totals against free memory on the node, and the full text of any `custom-script` role it
would run (those scripts run as root or SYSTEM).

**What you approve is what runs.** When you approve, VALOR computes the plan again; if it differs from the one you
looked at (because the spec or the cluster changed in between), the build is refused and you review the new plan.
Re-applying an unchanged spec makes no changes.

## The range page

Select a range in the tree. The title bar holds its actions: **Start** and **Shutdown** (shut down, reboot or force
stop every VM), **Login**, **Verify**, **Plan build** / **Plan changes**, and under **More** **Edit spec** and
**Destroy**. The sections:

| Section | What it shows |
|---|---|
| **Summary** | Status (VMs running, spec version, last build, last verification), the description, and every VM with its VMID, power state, segment, address, OS, size and services, with **Console**, **Start**, **Reboot** and **Shut down**. |
| **Map** | The topology: router, segments, hosts, allowed traffic and WireGuard access. Click a node for its details. This is also where you edit the range and open the range chat. |
| **Verification** | The last verification: each test (expected / observed), the baseline control x host matrix and, if the spec names frameworks, the compliance report. Before the first build: the tests it will run (explicit and automatic). |
| **Snapshots** | The range's snapshots: take, reset to, delete. |
| **Access** | WireGuard peers: status, configs and new keys (when the spec has `access.wireguard`). |
| **Spec** | The YAML the range was built from; **Edit** opens the editor. |
| **Journal** | A generated Markdown page: topology, hosts, network, policy, verification matrix, history. |
| **History** | Every build and verification with its result and duration. |

A VM in the tree has two sections: **Summary** (status, VMID, segment, address, OS, size, services) and **Console**,
with **Start**, **Shutdown** and **Console** in its title bar.

## Changing a built range

Change a range by editing its spec (**Edit spec**), on the map, or through the range chat. Map and chat changes go
into a **draft**: the map shows them in the plan colors (added, changed, rebuilt, removed), and nothing changes on the
cluster until you build the draft.

In the **Map** section (operators):

- **Add a VM**: name, segment, OS, size and services. For new services you choose who may reach them, and VALOR adds
  the matching traffic rules.
- **Edit VM** / **Remove VM**: resize a VM, add or remove its services, or delete it.
- **Add a network segment**: start from a preset (DMZ / public-facing services, users / LAN, servers, management,
  attacker lab, isolated network). VALOR suggests a free VLAN and private network; you can change both.
- **Edit segment** and **Traffic rules**: allow traffic between segments or hosts, or remove a rule. Segments listed
  in WireGuard's `reach` can be changed here too.
- **YAML** opens the draft as a spec to edit directly (**Save draft**).

**Review plan** shows the plan for the draft. Approving it saves the draft as the range's spec and builds it.
**Discard** throws the draft away.

## The range chat

The **Chat** button on the map opens a conversation about this range, kept per range and per person. It has three
modes:

| Mode | What the assistant does |
|---|---|
| **Question** | Answers questions about the range. It can read the range's status and spec and run **read-only** commands inside the VMs (for example `systemctl status nginx`, `ss -ltn`, `cat` a config). It cannot change anything. |
| **Plan** | Proposes a change as a draft on the map, with VALOR's plan for you to approve. |
| **Code** | Works inside the VMs: runs commands, reads and writes files, power and snapshots. It records lasting changes in the spec (as a draft) so a rebuild reproduces them. |

In Code mode, reading runs at once; anything that changes something waits for your approval on a card that shows the
exact command or file. VALOR decides what counts as read-only conservatively: anything it cannot be sure of needs
approval. **Run changes without asking (this conversation)** skips the approvals in one chat. **Stop** ends a
running answer.

Builds and teardowns are never started by the chat on its own: changes to the range's design reach the cluster through
a draft and plan -> approve. (The one exception is the automatic fix of a failed build, if an administrator turned
it on; see below.)

## When a build fails

The job shows which step failed, on which host, the last lines of output and often a hint. Finished parts are kept:
fix the cause and apply again, and the build continues where it is needed. If verification fails, the
**Verification** section shows which test or control failed.

The assistant can fix failed builds:

- **Ask the assistant to fix it** on a failed job opens a range chat in Code mode with the failure as its first
  message. It looks inside the VMs, fixes the cause (in the VMs, in the spec, or both) and tells you whether to
  rebuild.
- An administrator can make this automatic (**Datacenter -> AI providers**, *Failed builds go to*):
  - **Fix and rebuild on its own**: the assistant fixes and VALOR rebuilds, up to 3 rounds. It never approves a plan
    that would replace or remove a VM; those wait for a person.
  - **Fix on its own; the person rebuilds.**
  - **Prepare the fix; the person approves each change.**

The fix chat is listed under **Chats** and linked from the job.

## Getting into a range

**Consoles.** Select a VM in the tree -> **Console** (or **Console** next to it on the range's **Summary**): its
screen (noVNC) or serial console in the browser. The browser only talks to VALOR, never to Proxmox. **Type password** types the range login into the console.

**Range login.** Every range has one generated password (shown with **Login**, operators only):

| Hosts | User name |
|---|---|
| Linux | `valor` |
| Windows | `Administrator` |
| Windows in an Active Directory domain | `DOMAIN\Administrator` |

The password is stored encrypted, set inside the VMs over the guest agent, and can be changed with **New password...**
(VALOR sets the new one on every VM). SSH accepts keys only, so the password is for the consoles.

**WireGuard.** If the spec has `access.wireguard`, the range router runs a WireGuard server. The **Access** section lists
the peers with their connection status; **Config...** shows a peer's configuration with a QR code and a download.
Peers reach only the segments listed in `reach`, never the internet or your LAN through the tunnel. **New keys...**
replaces one peer's keys. The router listens on its LAN address; set `endpoint` in the spec if peers connect
through a port forward.

## Power and snapshots

**Start** and **Shutdown** in the range's title bar start, shut down, reboot or force-stop every VM of the range (the
router comes up first and goes down last); a VM's own title bar does the same for one VM. In **Snapshots** you take a snapshot of every VM at once, **Reset to this** (every VM goes back to
that snapshot and starts, then VALOR verifies the range again) or delete one. After every verified build VALOR takes
`valor-clean` itself, so you can always go back to the state that passed verification.

Some storage types (ZFS) only roll back to the newest snapshot; VALOR says so and offers to delete the newer ones.

## Removing a range

**Destroy** shows which VMs will be deleted and asks for confirmation. Only VMs in VALOR's range pool that carry the
range's tag are removed. The spec stays, so the range can be built again (**Plan build** in the same place).

## ISO library

**Datacenter -> ISO library** lists the installer media on the cluster's ISO storage.

- **Download to the cluster** from VALOR's catalog: official media where VALOR checks the publisher's signed
  checksum list before downloading, and Proxmox checks the file's checksum. Point releases are picked up
  automatically.
- Download by **URL (https)**, ideally with a checksum (without one, only HTTPS protects the download), or
  **Upload** a file from your computer. You can drop a checksum file (`SHA256SUMS` and similar) on the form to fill
  in the checksum.
- Administrators can delete media.

Uses: attach an ISO to a host as a CD-ROM (`iso:` in the spec), install Rocky or Alma from their ISO
(`install: iso`), and the Windows evaluation media the installer needs to build Windows templates
([INSTALL.md](INSTALL.md#more-operating-systems)).

## Blueprints: many copies of one range

A blueprint is a spec kept as a pattern, for example a lab every student gets.

1. **Datacenter -> Blueprints -> New blueprint** (paste a spec) or **From a range**.
2. **Deploy copies...**: choose **Numbered copies** (`lab-1`, `lab-2`, ...) or **One per student** (a name per
   student).
3. The preview shows every copy with its own name, free VLANs, private networks and WireGuard tunnel network. Hosts
   keep their position inside each network (`.10` stays `.10`). With one copy per student, each copy gets a WireGuard
   peer for its student.
4. **Plan** checks the copies against the cluster. Deploying writes exactly the previewed copies as ranges and,
   with **Build now**, builds them.

Each copy is a normal range afterwards. Deleting a blueprint keeps its copies.

## Compliance frameworks

A spec can name compliance frameworks (`compliance.frameworks`: NIST SP 800-171, NIST SP 800-53 Low or Moderate,
PCI DSS, HIPAA, CIS Level 1). VALOR then:

- requires a strong enough baseline (`linux-moderate`, with `windows-moderate` for Windows hosts),
- checks the design at plan time (segments holding regulated data have no internet, central logging, deny by
  default) and warns about gaps,
- reports per requirement in the range's **Verification** section: which control or test is the evidence, and its result.

Most requirements of every framework are organizational (policies, training, incident response) and are listed as
not covered. Results are always described as **aligned with** a framework's technical requirements, never as
compliant or certified. In the chat builder you can simply ask for a framework ("a CUI enclave aligned with NIST SP
800-171"). Details: [CONTENT.md](CONTENT.md#compliance-frameworks).

## Administration

Administrators find these in the **Datacenter** sections.

**Users**: create accounts with a role, change roles, disable, unlock, reset a password (the user must
choose a new one at the next sign-in) or reset MFA (the user sets it up again), delete.

**Audit log**: every sign-in, change, approval, console session and secret view, with who, when, from where
and the outcome. Searchable.

The settings:

| Section | |
|---|---|
| Directory (LDAP) | Sign in with LDAP or Active Directory: server, service account, user search, and the groups that map to admin / operator / viewer. **Test** checks a user lookup. |
| AI providers | One or more providers for the chats: OpenRouter (hosted models, Claude among them) or any OpenAI-compatible server (LM Studio, vLLM, Ollama's OpenAI API...). One is the default; a chat can pick another. Per provider: model, base URL, API key (encrypted, never shown again), max tokens per answer, and a silence timeout for slow local models. Also: the daily token budget per user (0 = unlimited), where failed builds go, and the AI tokens per user and day for the last 30 days. |
| Notifications | Where VALOR reports builds and problems: email (SMTP), Discord, Slack, Microsoft Teams, and in-app notifications (**Notifications** next to **Tasks**). Each channel gets problems only or all events; **Test** sends a test message. |
| Certificate | The certificate the web UI uses and when it expires; VALOR's version and instance. |

A local model needs a large context window: load it with at least 32,768 tokens of context (VALOR's prompt alone
is several thousand). Range chats in Code mode need a model with tool support.

**Datacenter -> Summary**: health at a glance, running and stopped VMs, free memory and storage, every range with
its last build and verification, the health checks (Proxmox API, the token's scope, templates, range network,
certificate, disk space, OS updates) and what VALOR uses on the cluster: bridges, reserved networks, default OS,
templates.

## Troubleshooting

| Symptom | What to do |
|---|---|
| "The plan changed since you reviewed it" | The spec or the cluster changed after you opened the plan. Review the new plan and approve that one. |
| A build waits for "the worker" | Another job is running; VALOR runs one infrastructure job at a time. It starts when the current one ends. |
| A host has no internet | Only segments with `internet: true` get internet access after the build. During the build every segment gets temporary access for package installs. |
| Two hosts cannot talk | Traffic between segments needs a policy rule (from the side that opens the connection). Hosts in the same segment reach each other without one. The range's Map shows the rules. |
| An OS is missing from the list | It has no template on this cluster yet. An administrator builds it with `./install.sh --template <os>` ([INSTALL.md](INSTALL.md#more-operating-systems)). |
| The chat builder says no provider is connected, or the model's context is too small | **Datacenter -> AI providers**; for a local model, load it with a larger context length. |
| A console stays black | The VM may still be booting, or it is a Windows VM before setup finished. **Reconnect**, or open the serial console for Linux hosts. |
| Locked out of the web UI | An admin unlocks the account (**Users**). If no admin can sign in: [break-glass commands](CLI.md#valor-web) on the Proxmox node. |
