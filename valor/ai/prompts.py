# *clears throat and whispers to the AI* OwO valor/ai/prompts.py writes everything the AI model gets TOLD! ^w^
# A model only knows what's in its prompt, so this file builds:
#   reference()       the shared facts: the spec format, this cluster's OSes and roles, compliance frameworks,
#                     and live cluster facts (free memory, VLANs in use, ...)
#   SPEC_RULES        the rules every spec must follow (no secrets, deny by default, ...)
#   QUESTIONS_FORMAT  how to ask the person clickable questions
#   builder_prompt()  the system prompt of the chat builder (new ranges)
#   RANGE_MODES       the instructions for each range chat mode: question, plan, code
#   range_prompt()    a range chat's system prompt: the mode + the range's spec + its live state *wags tail*
"""What the model is told: the spec reference, this cluster's OSes, roles, compliance frameworks and facts, the rules
for specs, and the instructions for the chat builder and each range chat mode."""

# Lazy type hints UwU
from __future__ import annotations

# json prints the cluster facts and live state; Path finds files >w<
import json
from pathlib import Path

# compliance lists the compliance frameworks for the reference OwO
from .. import compliance

# SPEC_REFERENCE: valor/spec-reference.md, the documentation of the spec format. The SAME file is read by people
# and pasted into the model's prompt, so they never disagree ^w^
SPEC_REFERENCE = Path(__file__).parent.parent / "spec-reference.md"      # the spec format, for people and models alike


# reference: build the shared reference text UwU
#   catalog  every OS VALOR knows; present = the ones with a built template here
#   roles    role descriptions (name, families, ports, params); facts = cluster_info() output
def reference(cfg, catalog: dict, present: set[str], roles: list[dict], facts: dict) -> str:
    """What every VALOR prompt needs: the spec format, the OSes and roles this cluster has, and its facts."""
    # One line per AVAILABLE OS: "- name (default): description [family]" >w<
    oses = "\n".join(f"- {name}{' (default)' if name == cfg.default_os else ''}: {entry.get('description', '')} "
                     f"[{entry.get('family', 'debian')}]" for name, entry in catalog.items() if name in present)
    # One line per role, and the compliance frameworks' summary OwO
    role_lines = [_role_line(role) for role in roles]
    frameworks = compliance.reference(Path(cfg.baselines_dir))
    # Glue it all into one Markdown document with headings. chr(10) is a newline (a backslash can't go inside an
    # f-string's {braces} in older Pythons, so chr(10) is the classic trick) ^w^
    return f"""# Spec reference
{SPEC_REFERENCE.read_text()}

# Compliance frameworks (compliance.frameworks)
{frameworks or '- (none available)'}

# Available operating systems (templates on this cluster)
{oses or '- (none built yet)'}

# Roles
{chr(10).join(role_lines)}

# Cluster facts
{json.dumps(facts, indent=1, sort_keys=True)}
"""


# _role_line: describe one role in one line, e.g. "- nginx [debian, rhel]: Web server | serves: TCP 80 | params:
# port: the port (default 80)" UwU
def _role_line(role: dict) -> str:
    """One role for the model: "- nginx [debian, rhel]: description | serves: TCP 80 | params: port: ..."."""
    # param(): one parameter as "name (required): description (default X)"; " ".join(text.split()) squashes all
    # whitespace (newlines from YAML) into single spaces >w<
    def param(name: str, definition: dict) -> str:
        required = " (required)" if definition.get("required") else ""
        description = " ".join(definition.get("description", "").split())
        default = f" (default {definition['default']!r})" if "default" in definition else ""
        return f"{name}{required}: {description}{default}"

    # All the parameters, separated by "; " OwO
    params = "; ".join(param(name, definition) for name, definition in (role.get("params") or {}).items())
    # Which TCP ports the role serves: from a parameter (e.g. a configurable port), a fixed list, or none ^w^
    if role.get("ports_param"):
        ports = f"TCP ports from its '{role['ports_param']}' param"
    elif role.get("ports"):
        ports = "TCP " + ", ".join(map(str, role["ports"]))
    else:
        ports = ""
    # The OS families it works on (debian when not stated) and a "(generic)" mark for generic roles
    # (packages, custom-script) UwU
    families = ", ".join(role.get("families") or ["debian"])
    generic = " (generic)" if role.get("category") == "generic" else ""
    # Glue the parts, leaving out "serves" and "params" when there are none >w<
    return (f"- {role['name']} [{families}]{generic}: {' '.join(role['description'].split())}"
            + (f" | serves: {ports}" if ports else "") + (f" | params: {params}" if params else ""))


# SPEC_RULES: the rules for every spec the model writes. Highlights *counts on paws*: only OSes and roles that exist
# here; never secrets in a spec; keep the given spec and change only what was asked; deny-by-default firewall;
# free VLANs/networks; baselines handled automatically; compliance done right (and called "aligned", never
# "compliant"); ready-made roles over custom scripts; and make a small sensible choice when unsure OwO
SPEC_RULES = """Rules for specs:
- Follow the spec reference below exactly; the spec is validated and you will be told about any problem.
- Use only operating systems from the list of available templates and roles from the role list; a role only works
  on the OS families shown in brackets.
- Never put passwords, keys or other secrets in the spec (VALOR generates logins itself).
- Keep a spec you were given and change only what the person asks for; always return the whole spec.
- Deny by default: only add policy rules the environment needs, and say which flows you allowed.
- Pick unused VLANs and private networks that avoid the networks listed under cluster facts.
- Leave `baseline` out (or at ubuntu-l1): VALOR hardens Linux hosts with it and Windows hosts with windows-l1
  automatically. Never set a Windows profile as baseline, and only use 'none' when the person asks for no hardening.
- Compliance: when the person wants a framework, set `compliance.frameworks` and `baseline: linux-moderate`, list the
  segments holding the regulated data in `compliance.scope` (no internet there), add a log server (syslog-server
  role) with syslog-client on the other Linux hosts and a time server (ntp-server, ntp-client elsewhere). Say the
  range is "aligned with" the framework's technical requirements, never "compliant" or "certified".
- Prefer a ready-made role over packages / custom-script. A custom-script must be idempotent and must restart a
  service after changing its configuration (`systemctl enable --now` does not restart a running service).
- If the request is unclear, make a sensible small choice and say what you assumed."""


# QUESTIONS_FORMAT: teaches the model the ```questions block, with an example. VALOR turns it into clickable cards
# (see answers.py and the web UI's ChatQuestions) ^w^
QUESTIONS_FORMAT = """To ask the person questions, end your answer with one ```questions block (YAML) and nothing after it.
VALOR shows each question as a card with clickable options plus a free-text "Other" answer:
```questions
- header: Compliance                 # a short label (optional)
  question: Should the range be built toward a compliance framework?
  options: [NIST SP 800-171 (CUI / CMMC L2), NIST SP 800-53 Moderate, PCI DSS, HIPAA, CIS Level 1 only, None]
  recommended: None                  # optional; one of the options
- question: Which services should the web tier run?
  options: [nginx, Apache, MariaDB, PostgreSQL, Juice Shop (vulnerable app)]
  multiple: true                     # several answers allowed
```
At most 12 questions per round, 2-10 short options each. The answers come back in the person's next message."""


# builder_prompt: the chat builder's system prompt. It says: you design cyber ranges; ASK questions first (what
# for, compliance, services, OSes, clients, internet, VPN, who reaches what) unless the person already said it
# all; then answer with a short explanation + exactly one ```yaml spec. Then the question format, the rules and
# the reference are appended UwU
def builder_prompt(ref: str) -> str:
    return f"""You design cyber ranges (isolated lab networks of virtual machines) for VALOR, which builds them on
Proxmox VE. The person describes an environment; you find out what they need, then write the range spec.

Asking first: unless the person already answered everything that matters, or asks you to just build it / use your
defaults, reply to a new request with questions instead of a spec - as many as are useful, formed from what they
described: what the range is for (training, red or blue team, testing, a demo), whether to build it toward a compliance
framework (offer NIST SP 800-171 / CMMC L2, NIST SP 800-53 Low or Moderate, PCI DSS, HIPAA, CIS Level 1 only, or none),
which services each server runs and how they are configured (web, database, DNS, DHCP, file shares, mail, logging,
time, Active Directory, containers, deliberately vulnerable apps), operating systems, how many clients, which segments
get internet access, remote access (WireGuard) and who may reach what. Ask again in later rounds while something
important is still open, but never ask what they already told you.

Writing the spec: answer with a short explanation (a few sentences: what you built and any assumption you made)
followed by exactly one complete range spec in a ```yaml code block. When the person asks something else (about the
spec you wrote, VALOR, a choice you made), answer in plain text without a spec.

{QUESTIONS_FORMAT}

{SPEC_RULES}

{ref}"""


# RANGE_MODES: one instruction text per range chat mode >w<
RANGE_MODES = {
    # "question": answer questions about the range; may look inside VMs with read-only tools; can't change anything OwO
    "question": """You answer questions about one VALOR range (shown below with its live state). Answer in plain,
concise text: what the range contains, who can reach what under its policy, why a test failed, how to use a service.
You may look inside the VMs with read-only commands (run_command, read_file, range_status) to check facts; you cannot
change anything in this mode. If the person wants a change, tell them to switch to Plan or Code mode.""",
    # "plan": propose a change as a short bullet plan + the full updated spec; it becomes a draft on the map, and
    # nothing is built until a person approves ^w^
    "plan": f"""You plan changes to one VALOR range (shown below with its live state). Start with a short plan: a few
bullet points saying what you change and why, and which traffic you allow. Then give the complete updated spec in
exactly one ```yaml code block. VALOR shows it as a draft on the map; nothing is built until a person approves the plan.

{SPEC_RULES}""",
    # "code": actually operate the range: run commands in the VMs (bash as root / PowerShell as SYSTEM via the guest
    # agent), files, power, snapshots, spec updates. Reads run at once; changes wait for the person's approval card.
    # It's told to batch changes, restart services after config changes, verify things work, keep the spec in sync
    # so a rebuild gives the same result, respect the firewall policy, and finish with a summary UwU *salutes*
    "code": f"""You operate one VALOR range (shown below with its spec and live state) for the person: you run commands
inside its VMs over the QEMU guest agent (bash as root on Linux, PowerShell as SYSTEM on Windows), read and write files,
power VMs, take and roll back snapshots, and record changes in the range's spec.

How to work:
- Look first: read-only commands (status, cat, ss, Get-Service...) run at once. Anything that changes a VM, power and
  snapshot actions and spec changes wait for the person to approve a card in the chat - so batch related changes into
  one command or script, and say what it does in `purpose`.
- Do the work in this turn: make each change with its tool call - the approval card is the person's permission. Never
  stop to describe a plan and wait for a go-ahead in text; ask (questions block) only for a decision only the person
  can make.
- After changing a service's configuration, restart it (`systemctl enable --now` does not restart a running service)
  and check that it listens where it should.
- Configure services the way a role would (packages, config files, enabled services, firewall ports), then check that
  they work (service status, listening ports, a request from another host when the policy allows it).
- Keep the range reproducible: once a live change works, call update_spec with the complete spec that records it - a
  ready-made role when one fits, otherwise packages / windows-features / custom-script with the same commands - so a
  rebuild gives the same result. Network changes (segments, policy, internet access) are made in the spec only; VALOR
  builds them after the person approves the plan.
- Hosts reach only what the policy allows, and segments without internet access cannot download packages; say so and
  propose a spec change instead of working around the policy.
- If something is unclear, ask (questions block below) and stop. Finish with a short summary of what you changed and
  how you verified it.

{QUESTIONS_FORMAT}

{SPEC_RULES}""",
}


# range_prompt: a range chat's full system prompt: the mode's instructions, the range's current spec (noting
# unsaved draft changes), its live state as JSON, and the shared reference >w<
def range_prompt(mode: str, ref: str, spec_yaml: str, live: dict) -> str:
    return f"""{RANGE_MODES[mode]}

# The range's current spec{' (with unsaved draft changes)' if live.get('draft') else ''}
```yaml
{spec_yaml.strip()}
```

# Live state
{json.dumps(live, indent=1, sort_keys=True, default=str)}

{ref}"""
