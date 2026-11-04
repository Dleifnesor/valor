# *holds up the most important paper of all* OwO valor/spec.py defines the RANGE SPEC! ^w^
# A range spec (YAML) is the single description of a lab: segments (VLAN + subnet), hosts in them, the firewall
# policy between segments, optional WireGuard access and compliance frameworks. EVERYTHING else (validation, the
# plan, the router's firewall, the journal, the tests) is derived from it. The models are STRICT: unknown keys are
# errors, so a typo is reported instead of silently ignored. This file also loads specs safely, fills defaults,
# hashes them, and derives the connectivity tests *wags tail*
"""Range specification (YAML): models, safe loading, normalization, hashing and test generation.

A range spec is the single description of a lab: segments (VLAN + subnet), hosts in them, the firewall policy
between segments, optional WireGuard access and compliance frameworks. Everything else - validation against the
cluster, the build plan, the router's firewall, the journal - is derived from it. The models below are strict
(unknown keys are errors), so a typo in a spec is reported instead of silently ignored.

The spec format for people (and for the AI) is documented in spec-reference.md next to this file.
"""

# Lazy type hints UwU
from __future__ import annotations

# hashlib/json (the spec hash), ipaddress (networks and addresses), re (names, ports), Path, Literal >w<
import hashlib
import ipaddress
import json
import re
from pathlib import Path
from typing import Literal

# PyYAML (safe_load only) and pydantic: BaseModel for the models, ConfigDict/Field for rules, ValidationError, and
# the validator decorators OwO
import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

# SpecError = "the spec is wrong" with a list of problems ^w^
from .errors import SpecError

# API_VERSION: the spec format version. ROUTER: the router's reserved host name. NAME: the name pattern (2-15 chars,
# a valid DNS label). MAX_SPEC_BYTES: 256 KiB max. INTERNET_PROBE: the default "is the internet reachable?" target UwU
API_VERSION = "valor/v1"
ROUTER = "rtr"                                      # the router's host name in every range (reserved)
NAME = r"^[a-z][a-z0-9-]{0,13}[a-z0-9]$"            # 2-15 chars, DNS label
MAX_SPEC_BYTES = 256 * 1024
INTERNET_PROBE = ("1.1.1.1", 443)                   # default "is the internet reachable" target


# _Strict: the base of every spec model. extra="forbid" makes unknown keys errors; populate_by_name lets Python
# code use `from_` for the YAML key `from` (a Python keyword) >w<
class _Strict(BaseModel):
    """Base for every spec model: unknown keys are errors, and `from` can be written as `from_` in Python."""
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


# ParamValue: what a role parameter may be: text, a number, true/false, or a list of texts OwO
ParamValue = str | int | bool | list[str]


# ---- The parts of a spec ----------------------------------------------------------------------------------

# RoleRef: a service on a host: the role's name and its parameters (parameter names must be lower_snake_case) ^w^
class RoleRef(_Strict):
    """A service on a host (roles/<name>/), with its parameters."""
    name: str = Field(pattern=r"^[a-z][a-z0-9-]{0,40}$")
    params: dict[str, ParamValue] = Field(default_factory=dict)

    @field_validator("params")
    @classmethod
    def _param_names(cls, params: dict) -> dict:
        for key in params:
            if not re.fullmatch(r"[a-z][a-z0-9_]{0,40}", key):
                raise ValueError(f"role parameter name '{key}' must be lower_snake_case")
        return params


# Segment: one network: name, VLAN tag (2-4094), subnet, internet access (off by default) and a description UwU
class Segment(_Strict):
    """One network of the range: a VLAN on the range bridge with its own subnet. The router takes the first
    address of the subnet as the segment's gateway."""
    name: str = Field(pattern=NAME)
    vlan: int = Field(ge=2, le=4094)
    cidr: ipaddress.IPv4Network
    internet: bool = False
    description: str = ""

    # gateway: the router's address in the segment = the first usable address (e.g. .1) >w<
    @property
    def gateway(self) -> ipaddress.IPv4Address:
        return next(self.cidr.hosts())

    # The subnet must be private and between /16 and /29 OwO
    @field_validator("cidr")
    @classmethod
    def _cidr(cls, cidr: ipaddress.IPv4Network) -> ipaddress.IPv4Network:
        if not cidr.is_private:
            raise ValueError("segment networks must use private address space")
        if not 16 <= cidr.prefixlen <= 29:
            raise ValueError("segment prefix length must be between /16 and /29")
        return cidr


# Host: one VM ^w^
#   name / segment / address  who, where, which IP
#   os                        an OS from the catalog (None = the default OS, filled in by normalize)
#   cores / memory / disk     size, with limits (1-16 vCPU, 512 MiB-64 GiB, 8-500 GiB)
#   roles                     its services; description = free text
#   iso                       an ISO from the library to attach as a CD (a safe file name)
#   install                   clone the template (fast) or install from the OS's installer ISO
#   nested                    pass hardware virtualization through (to run a hypervisor inside)
class Host(_Strict):
    name: str = Field(pattern=NAME)
    segment: str
    address: ipaddress.IPv4Address
    os: str | None = None
    cores: int = Field(1, ge=1, le=16)
    memory: int = Field(1024, ge=512, le=65536, description="MiB")
    disk: int = Field(10, ge=8, le=500, description="GiB")
    roles: list[RoleRef] = Field(default_factory=list)
    description: str = ""
    iso: str | None = Field(None, pattern=r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,200}\.iso$",
                            description="an ISO from the ISO library attached as a CD-ROM")
    install: Literal["template", "iso"] = Field("template", description="clone the OS template (fast) or install "
                                                "from the OS's installer ISO (Rocky/Alma, kickstart)")
    nested: bool = Field(False, description="pass hardware virtualization through (CPU type host), e.g. to run "
                                             "a hypervisor such as Proxmox VE inside the host")


# Router: the router's OS and size (VALOR always makes one) UwU
class Router(_Strict):
    """Size and OS of the range router (VALOR always creates one; it routes, NATs and enforces the policy)."""
    os: str | None = None
    cores: int = Field(1, ge=1, le=8)
    memory: int = Field(1024, ge=512, le=8192)
    disk: int = Field(10, ge=8, le=100)


# Rule: one firewall rule: from (a segment or host) -> to, protocol, ports, description >w<
class Rule(_Strict):
    """A firewall rule: traffic from a segment or host to another segment or host. Everything not allowed by a
    rule is blocked between segments (default deny)."""
    from_: str = Field(alias="from")
    to: str
    proto: Literal["tcp", "udp", "icmp", "any"] = "tcp"
    ports: list[int | str] = Field(default_factory=list)
    description: str = ""

    # _ports: each port is a number (443) or a range ("8000-8100"); "80-80" becomes 80; out-of-range values fail OwO
    @field_validator("ports")
    @classmethod
    def _ports(cls, ports: list) -> list:
        """Ports are 443 or "8000-8100"; a range like "80-80" is stored as the single port 80."""
        out = []
        for port in ports:
            text = str(port)
            match = re.fullmatch(r"(\d{1,5})(?:-(\d{1,5}))?", text)
            if not match:
                raise ValueError(f"invalid port '{text}' (use 443 or 8000-8100)")
            low, high = int(match.group(1)), int(match.group(2) or match.group(1))
            if not (1 <= low <= high <= 65535):
                raise ValueError(f"port out of range: '{text}'")
            out.append(low if low == high else f"{low}-{high}")
        return out


# Test: a connectivity check written in the spec: from a host to a host/IP/"internet", tcp (with a port) or icmp,
# expecting open or closed ^w^
class Test(_Strict):
    """A connectivity check that verify runs from one host: `expect: open` or `closed`."""
    name: str | None = None
    from_: str = Field(alias="from")
    to: str
    proto: Literal["tcp", "icmp"] = "tcp"
    port: int | None = Field(None, ge=1, le=65535)
    expect: Literal["open", "closed"]


# WireGuard: VPN access: UDP port, tunnel network (private, /16-/29), peers (1-100 unique lowercase names), which
# segments they may reach (default all), and an optional endpoint (e.g. a port forward) UwU
class WireGuard(_Strict):
    """Remote access to the range through a WireGuard tunnel that ends on the range router."""
    port: int = Field(51820, ge=1024, le=65535, description="UDP port on the router's uplink address")
    network: ipaddress.IPv4Network = Field(ipaddress.IPv4Network("10.250.0.0/24"),
                                           description="tunnel addresses (the router takes the first)")
    peers: list[str] = Field(min_length=1, max_length=100, description="one config per person or device")
    reach: list[str] = Field(default_factory=list, description="segments peers may reach (default: all)")
    endpoint: str = Field("", max_length=253, pattern=r"^$|^[A-Za-z0-9.-]+(:\d{1,5})?$",
                          description="address peers connect to, e.g. a port forward (default: the router's)")

    @field_validator("network")
    @classmethod
    def _net(cls, network: ipaddress.IPv4Network) -> ipaddress.IPv4Network:
        if not network.is_private or not 16 <= network.prefixlen <= 29:
            raise ValueError("the tunnel network must be private address space between /16 and /29")
        return network

    @field_validator("peers")
    @classmethod
    def _peers(cls, peers: list[str]) -> list[str]:
        for name in peers:
            if not re.fullmatch(r"[a-z][a-z0-9-]{0,30}", name):
                raise ValueError(f"peer name '{name}' must be lowercase letters, digits or dashes")
        if len(set(peers)) != len(peers):
            raise ValueError("peer names must be unique")
        return peers


# Access: the access methods (only WireGuard so far) >w<
class Access(_Strict):
    wireguard: WireGuard | None = None


# FRAMEWORKS: the compliance frameworks VALOR knows OwO
FRAMEWORKS = ("nist-800-171", "nist-800-53-moderate", "nist-800-53-low", "pci-dss", "hipaa", "cis-l1")


# Compliance: which frameworks (each at most once), and which segments hold the regulated data ^w^
class Compliance(_Strict):
    frameworks: list[Literal[FRAMEWORKS]] = Field(default_factory=list, max_length=len(FRAMEWORKS),
                                                  description="frameworks the range is built toward and reported on")
    scope: list[str] = Field(default_factory=list, max_length=16,
                             description="segments holding the regulated data (CUI, cardholder data, ePHI)")

    @field_validator("frameworks")
    @classmethod
    def _unique(cls, frameworks: list) -> list:
        if len(set(frameworks)) != len(frameworks):
            raise ValueError("each framework only once")
        return frameworks


# ---- The whole spec ---------------------------------------------------------------------------------------

# RangeSpec: a whole range. Field rules live on the models above; rules spanning several parts are in
# _cross_checks UwU
class RangeSpec(_Strict):
    """A complete range. Field-level rules live on the models above; rules that need several parts of the spec at
    once (a host's address must be in its segment, ...) are checked in `_cross_checks`."""
    # The fields: format version, name, description, baseline ("none" or a profile name), router, 1-16 segments, 1-40
    # hosts, policy rules, extra tests, whether to derive tests automatically, access and compliance >w<
    apiVersion: Literal["valor/v1"] = API_VERSION
    name: str = Field(pattern=NAME)
    description: str = ""
    baseline: str = Field("ubuntu-l1", pattern=r"^(none|[a-z][a-z0-9-]{0,40})$")
    router: Router = Field(default_factory=Router)
    segments: list[Segment] = Field(min_length=1, max_length=16)
    hosts: list[Host] = Field(min_length=1, max_length=40)
    policy: list[Rule] = Field(default_factory=list)
    tests: list[Test] = Field(default_factory=list)
    auto_tests: bool = True
    access: Access | None = None
    compliance: Compliance | None = None

    # _cross_checks runs after the fields are valid. It collects ALL problems (so the author or the AI sees the whole
    # list at once) and raises them together OwO
    @model_validator(mode="after")
    def _cross_checks(self) -> "RangeSpec":
        """All rules that span several parts of the spec. Every problem is collected, so the author (or the AI)
        sees the whole list at once instead of fixing one error per round."""
        problems = (self._name_problems() + self._segment_problems() + self._host_problems()
                    + self._policy_problems() + self._wireguard_problems() + self._compliance_problems()
                    + self._test_problems())
        if problems:
            raise ValueError("; ".join(problems))
        return self

    # _name_problems: unique segment and host names, "rtr" reserved, and no name used for both a segment and a host ^w^
    def _name_problems(self) -> list[str]:
        problems = []
        segment_names = [segment.name for segment in self.segments]
        host_names = [host.name for host in self.hosts]
        if len(set(segment_names)) != len(segment_names):
            problems.append("segment names must be unique")
        if len(set(host_names)) != len(host_names):
            problems.append("host names must be unique")
        if ROUTER in host_names:
            problems.append(f"'{ROUTER}' is reserved for the range router (created automatically)")
        both = set(segment_names) & set(host_names)
        if both:
            problems.append(f"names used for both a segment and a host: {sorted(both)}")
        return problems

    # _segment_problems: one VLAN per segment, and no overlapping subnets UwU
    def _segment_problems(self) -> list[str]:
        problems = []
        vlans = [segment.vlan for segment in self.segments]
        if len(set(vlans)) != len(vlans):
            problems.append("each segment needs its own VLAN")
        for i, first in enumerate(self.segments):
            for second in self.segments[i + 1:]:
                if first.cidr.overlaps(second.cidr):
                    problems.append(f"segments {first.name} and {second.name} overlap ({first.cidr} / {second.cidr})")
        return problems

    # _host_problems: each host's segment must exist; its address must be usable in that subnet, not the gateway, and
    # not shared >w<
    def _host_problems(self) -> list[str]:
        """Each host's address must be usable in its segment, not the router's, and not taken by another host."""
        problems = []
        segments = {segment.name: segment for segment in self.segments}
        owner_of_address: dict = {}
        for host in self.hosts:
            segment = segments.get(host.segment)
            if not segment:
                problems.append(f"host {host.name}: unknown segment '{host.segment}'")
                continue
            net = segment.cidr
            if host.address not in net or host.address in (net.network_address, net.broadcast_address):
                problems.append(f"host {host.name}: {host.address} is not a usable address in {net}")
            if host.address == segment.gateway:
                problems.append(f"host {host.name}: {host.address} is the router's address in segment {segment.name}")
            if host.address in owner_of_address:
                problems.append(f"hosts {owner_of_address[host.address]} and {host.name} share {host.address}")
            owner_of_address[host.address] = host.name
        return problems

    # _policy_problems: rule endpoints must exist; ports only (and always) for tcp/udp; and no rules inside one
    # segment (that traffic never passes the router, so it can't be filtered) OwO
    def _policy_problems(self) -> list[str]:
        problems = []
        endpoints = {segment.name for segment in self.segments} | {host.name for host in self.hosts}
        for i, rule in enumerate(self.policy):
            for end in (rule.from_, rule.to):
                if end not in endpoints:
                    problems.append(f"policy[{i}]: unknown segment or host '{end}'")
            if rule.proto in ("icmp", "any") and rule.ports:
                problems.append(f"policy[{i}]: ports only apply to tcp/udp")
            if rule.proto in ("tcp", "udp") and not rule.ports:
                problems.append(f"policy[{i}]: tcp/udp rules need at least one port")
            # The router only sees traffic that crosses segments, so a rule inside one segment would be a no-op.
            from_segment, to_segment = self.segment_of(rule.from_), self.segment_of(rule.to)
            if from_segment and to_segment and from_segment == to_segment:
                problems.append(f"policy[{i}]: {rule.from_} and {rule.to} are in the same segment ({from_segment}); "
                                "traffic inside a segment never reaches the router, so it cannot be filtered")
        return problems

    # _wireguard_problems: reach must name real segments; the tunnel network must not overlap a segment and must have
    # room for every peer (minus network, broadcast and the router's address) ^w^
    def _wireguard_problems(self) -> list[str]:
        wireguard = self.access.wireguard if self.access else None
        if not wireguard:
            return []
        problems = []
        segment_names = {segment.name for segment in self.segments}
        for name in wireguard.reach:
            if name not in segment_names:
                problems.append(f"access.wireguard.reach: unknown segment '{name}'")
        for segment in self.segments:
            if segment.cidr.overlaps(wireguard.network):
                problems.append(f"access.wireguard.network {wireguard.network} overlaps segment {segment.name} "
                                f"({segment.cidr})")
        # Network and broadcast addresses aren't usable and the router takes the first address.
        if len(wireguard.peers) > wireguard.network.num_addresses - 3:
            problems.append(f"access.wireguard.network {wireguard.network} is too small for "
                            f"{len(wireguard.peers)} peers")
        return problems

    # _compliance_problems: scope must name real segments UwU
    def _compliance_problems(self) -> list[str]:
        if not self.compliance:
            return []
        segment_names = {segment.name for segment in self.segments}
        return [f"compliance.scope: unknown segment '{name}'"
                for name in self.compliance.scope if name not in segment_names]

    # _test_problems: a test starts from a host and goes to a host, an IPv4 address or "internet"; tcp tests need a
    # port (except to the internet, which uses the probe's) >w<
    def _test_problems(self) -> list[str]:
        problems = []
        host_names = {host.name for host in self.hosts}
        for i, test in enumerate(self.tests):
            if test.from_ not in host_names:
                problems.append(f"tests[{i}]: 'from' must be a host, got '{test.from_}'")
            if test.to not in host_names and test.to != "internet":
                try:
                    ipaddress.IPv4Address(test.to)
                except ValueError:
                    problems.append(f"tests[{i}]: 'to' must be a host, an IPv4 address or 'internet'")
            if test.proto == "tcp" and test.port is None and test.to != "internet":
                problems.append(f"tests[{i}]: tcp tests need a port")
        return problems

    # ---- Lookups used across the engine OwO
    # ---- Lookups used across the engine ----
    # segment / host: find one by name ^w^
    def segment(self, name: str) -> Segment:
        return next(segment for segment in self.segments if segment.name == name)

    def host(self, name: str) -> Host:
        return next(host for host in self.hosts if host.name == name)

    # segment_of: the segment of a policy endpoint (the segment itself, or a host's segment) UwU
    def segment_of(self, endpoint: str) -> str | None:
        """The segment a policy endpoint lives in: the endpoint itself if it names a segment, else its host's."""
        if any(segment.name == endpoint for segment in self.segments):
            return endpoint
        for host in self.hosts:
            if host.name == endpoint:
                return host.segment
        return None

    # endpoint_cidr: an endpoint as a firewall network: a segment's subnet or a host's /32 >w<
    def endpoint_cidr(self, endpoint: str) -> str:
        """A policy endpoint as a network for the firewall: the segment's subnet, or the host's /32."""
        for segment in self.segments:
            if segment.name == endpoint:
                return str(segment.cidr)
        return f"{self.host(endpoint).address}/32"

    # hosts_in: the hosts of one segment OwO
    def hosts_in(self, segment: str) -> list[Host]:
        return [host for host in self.hosts if host.segment == segment]

    # reaches: may host src connect to host dst at all? Same segment, or a rule between them (by host or segment) ^w^
    def reaches(self, src: str, dst: str) -> bool:
        """Whether host src may open connections to host dst at all: same segment, or a policy rule between them."""
        source, target = self.host(src), self.host(dst)
        if source.segment == target.segment:
            return True
        return any(rule.from_ in (source.name, source.segment) and rule.to in (target.name, target.segment)
                   for rule in self.policy)


# ---- Loading, normalizing and hashing ---------------------------------------------------------------------

# normalize: fill in defaults that come from the engine config (the default OS for the router and hosts without
# one), and validate again UwU
def normalize(spec: RangeSpec, default_os: str) -> RangeSpec:
    """Fill defaults that depend on engine configuration (OS)."""
    data = spec.model_dump(by_alias=True)
    if not data["router"].get("os"):
        data["router"]["os"] = default_os
    for host in data["hosts"]:
        if not host.get("os"):
            host["os"] = default_os
    return RangeSpec.model_validate(data)


# canonical: the spec as compact, key-sorted JSON (the input of spec_hash). Newer optional fields are left out while
# they have their default, so older specs keep the SAME hash after VALOR learns a new field >w<
def canonical(spec: RangeSpec) -> str:
    """The spec as compact, key-sorted JSON: the input of spec_hash. Fields added after the first release are
    left out while they have their default value, so the hash of an older spec (and the VMs tagged with it) does
    not change just because VALOR learned a new field."""
    data = spec.model_dump(mode="json", by_alias=True)
    for key in ("access", "compliance"):
        if data.get(key) is None:
            data.pop(key, None)
    for host in data["hosts"]:
        if host.get("iso") is None:
            host.pop("iso", None)
        if host.get("install") == "template":
            host.pop("install", None)
        if host.get("nested") is False:
            host.pop("nested", None)
    return json.dumps(data, sort_keys=True, separators=(",", ":"))


# spec_hash: SHA-256 of the canonical JSON = the spec's version id (VMs are stamped with it) OwO
def spec_hash(spec: RangeSpec) -> str:
    return hashlib.sha256(canonical(spec).encode()).hexdigest()


# resolve_spec_path: find a CLI spec argument (a bare name is looked for in the ranges folder first). SAFETY: the
# file must be a .yaml INSIDE the ranges folder (no reading other files) and not too big ^w^
def resolve_spec_path(path: str | Path, ranges_dir: Path) -> Path:
    """The spec file for a CLI argument: a bare name is looked up in the ranges directory first. Whatever the
    path, the resolved file must be a .yaml inside the ranges directory (no reading arbitrary files)."""
    candidate = Path(path)
    if not candidate.is_absolute():
        in_ranges = ranges_dir / candidate
        in_cwd = Path.cwd() / candidate
        candidate = in_ranges if in_ranges.exists() or not in_cwd.exists() else in_cwd
    try:
        resolved = candidate.resolve(strict=True)
    except FileNotFoundError:
        raise SpecError(f"spec file not found: {path}", hint=f"Specs live in {ranges_dir}/<name>.yaml")
    if not resolved.is_relative_to(ranges_dir.resolve()):
        raise SpecError(f"spec files must be inside {ranges_dir}", hint="Write the spec to ranges/<name>.yaml")
    if resolved.suffix not in (".yaml", ".yml") or not resolved.is_file():
        raise SpecError("spec must be a .yaml file")
    if resolved.stat().st_size > MAX_SPEC_BYTES:
        raise SpecError("spec file is too large")
    return resolved


# parse_spec: YAML text -> RangeSpec. Error messages never echo file content (the engine can read secrets) UwU
def parse_spec(text: str) -> RangeSpec:
    """Parse YAML text. Error messages never echo file content (the engine can read secrets)."""
    try:
        # YAML syntax errors report only line and column >w<
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        mark = getattr(e, "problem_mark", None)
        where = f" at line {mark.line + 1}, column {mark.column + 1}" if mark else ""
        raise SpecError(f"YAML syntax error{where}: {getattr(e, 'problem', None) or 'invalid YAML'}")
    # The top level must be a mapping OwO
    if not isinstance(data, dict):
        raise SpecError("spec must be a YAML mapping (apiVersion, name, segments, hosts, ...)")
    # Turn pydantic's errors into {location, message} entries (with a friendly tip for the classic "10.340.0.0" octet
    # mistake) ^w^
    try:
        return RangeSpec.model_validate(data)
    except ValidationError as e:
        errors = []
        for err in e.errors(include_url=False, include_input=False):
            location = ".".join(str(part) for part in err["loc"]) or "(spec)"
            message = err["msg"].removeprefix("Value error, ")
            if err["type"] in ("ip_v4_network", "ip_v4_address", "ip_v4_interface"):
                # The most common AI mistake: putting a 3-digit VLAN number into an octet.
                message += " (each of the four numbers is 0-255: for VLAN 340 e.g. 10.34.0.0/24, not 10.340.0.0/24)"
            errors.append({"location": location, "message": message})
        raise SpecError(f"spec has {len(errors)} problem(s)", errors)


# load_spec: resolve, read, parse and normalize; returns (spec, file path) UwU
def load_spec(path: str | Path, ranges_dir: Path, default_os: str) -> tuple[RangeSpec, Path]:
    spec_file = resolve_spec_path(path, ranges_dir)
    return normalize(parse_spec(spec_file.read_text()), default_os), spec_file


# ---- Connectivity tests (the comment below lists the three kinds VALOR derives) >w<
# ---- Connectivity tests -----------------------------------------------------------------------------------
#
# verify runs a list of tests. Besides the ones written in the spec, VALOR derives tests from the spec itself
# (auto_tests, on by default):
#   policy     every rule is tried once and must be open (first port of the rule)
#   isolation  SSH between every pair of segments that no rule connects must be blocked
#   egress     each segment's internet access must match its `internet` flag

# effective_tests: the spec's own tests first, then (with auto_tests) policy, isolation and egress tests OwO
def effective_tests(spec: RangeSpec, probe: tuple[str, int] = INTERNET_PROBE) -> list[dict]:
    """Explicit tests plus, with auto_tests, tests derived from the policy and egress settings.
    probe: the public host:port that stands for "the internet" (configurable per installation)."""
    tests = _TestList()
    _add_spec_tests(spec, tests, probe)
    if spec.auto_tests:
        allowed = _add_policy_tests(spec, tests)
        _add_isolation_tests(spec, tests, allowed)
        _add_egress_tests(spec, tests, probe)
    return tests.items


# _TestList: keeps tests in order and drops a later test with the same (from, to, proto, port), so an explicit
# test always wins over a derived one ^w^
class _TestList:
    """Tests in the order they were added; a second test of the same (from, to, proto, port) is dropped, so an
    explicit test always wins over a derived one."""

    def __init__(self):
        self.items: list[dict] = []

    def add(self, name, src, dst, proto, port, expect, origin) -> None:
        key = (src, dst, proto, port)
        if any((test["from"], test["to"], test["proto"], test["port"]) == key for test in self.items):
            return
        self.items.append({"name": name, "from": src, "to": dst, "proto": proto, "port": port,
                           "expect": expect, "origin": origin})


# _first_host: the host that stands in for an endpoint: the host itself, or the first host of the segment UwU
def _first_host(spec: RangeSpec, endpoint: str) -> str | None:
    """The host that stands for a policy endpoint in a test: the host itself, or the first host of the segment."""
    if any(host.name == endpoint for host in spec.hosts):
        return endpoint
    members = spec.hosts_in(endpoint)
    return members[0].name if members else None


# _add_spec_tests: the spec's tests, with internet tcp tests using the probe's port and a generated name when none
# is given >w<
def _add_spec_tests(spec: RangeSpec, tests: _TestList, probe: tuple[str, int]) -> None:
    for test in spec.tests:
        port = test.port
        if test.to == "internet" and test.proto == "tcp":
            port = port or probe[1]
        name = test.name or f"{test.from_} -> {test.to} {test.proto}{'/' + str(port) if port else ''} {test.expect}"
        tests.add(name, test.from_, test.to, test.proto, port, test.expect, "spec")


# _add_policy_tests: one "must be open" test per rule (tcp on its first port, or icmp), and a record of what each
# segment pair is allowed (for the isolation tests) OwO
def _add_policy_tests(spec: RangeSpec, tests: _TestList) -> dict[tuple[str, str], set]:
    """One "open" test per rule. Returns what the policy allows per (from segment, to segment) pair, which the
    isolation tests need: {("users", "servers"): {443, "8000-8100", "any"}, ...}."""
    allowed: dict[tuple[str, str], set] = {}
    for rule in spec.policy:
        pair = (spec.segment_of(rule.from_), spec.segment_of(rule.to))
        allowed.setdefault(pair, set())
        if rule.proto == "any":
            allowed[pair].add("any")
        allowed[pair].update(rule.ports)
        src, dst = _first_host(spec, rule.from_), _first_host(spec, rule.to)
        if not src or not dst:
            continue
        if rule.proto == "tcp" and rule.ports:
            port = int(str(rule.ports[0]).split("-")[0])        # a range "8000-8100" is tried on its first port
            tests.add(f"policy allows {src} -> {dst} tcp/{port}", src, dst, "tcp", port, "open", "policy")
        elif rule.proto == "icmp":
            tests.add(f"policy allows {src} -> {dst} icmp", src, dst, "icmp", None, "open", "policy")
    return allowed


# _add_isolation_tests: for every pair of different segments, SSH (tcp/22) must be BLOCKED unless a rule allows
# it ^w^
def _add_isolation_tests(spec: RangeSpec, tests: _TestList, allowed: dict[tuple[str, str], set]) -> None:
    """SSH (tcp/22) between two segments must be blocked unless a rule allows it."""
    for source in spec.segments:
        for target in spec.segments:
            if source.name == target.name:
                continue
            src, dst = _first_host(spec, source.name), _first_host(spec, target.name)
            if not src or not dst:
                continue
            allowed_here = allowed.get((source.name, target.name), set())
            if "any" in allowed_here or 22 in allowed_here:
                continue
            tests.add(f"isolation: {src} ({source.name}) -> {dst} ({target.name}) tcp/22 blocked",
                      src, dst, "tcp", 22, "closed", "isolation")


# _add_egress_tests: each segment's internet access must match its `internet` flag UwU *checks every door*
def _add_egress_tests(spec: RangeSpec, tests: _TestList, probe: tuple[str, int]) -> None:
    for segment in spec.segments:
        src = _first_host(spec, segment.name)
        if not src:
            continue
        verdict = "allowed" if segment.internet else "blocked"
        tests.add(f"egress: {segment.name} internet {verdict}",
                  src, "internet", "tcp", probe[1], "open" if segment.internet else "closed", "egress")
