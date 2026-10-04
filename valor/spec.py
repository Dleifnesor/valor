"""Range specification (YAML): models, safe loading, normalization, hashing and test generation."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from .errors import SpecError

API_VERSION = "valor/v1"
ROUTER = "rtr"
NAME = r"^[a-z][a-z0-9-]{0,13}[a-z0-9]$"            # 2-15 chars, DNS label
MAX_SPEC_BYTES = 256 * 1024
INTERNET_PROBE = ("1.1.1.1", 443)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


ParamValue = str | int | bool | list[str]


class RoleRef(_Strict):
    name: str = Field(pattern=r"^[a-z][a-z0-9-]{0,40}$")
    params: dict[str, ParamValue] = Field(default_factory=dict)

    @field_validator("params")
    @classmethod
    def _param_names(cls, v: dict) -> dict:
        for k in v:
            if not re.fullmatch(r"[a-z][a-z0-9_]{0,40}", k):
                raise ValueError(f"role parameter name '{k}' must be lower_snake_case")
        return v


class Segment(_Strict):
    name: str = Field(pattern=NAME)
    vlan: int = Field(ge=2, le=4094)
    cidr: ipaddress.IPv4Network
    internet: bool = False
    description: str = ""

    @property
    def gateway(self) -> ipaddress.IPv4Address:
        return next(self.cidr.hosts())

    @field_validator("cidr")
    @classmethod
    def _cidr(cls, v: ipaddress.IPv4Network) -> ipaddress.IPv4Network:
        if not v.is_private:
            raise ValueError("segment networks must use private address space")
        if not 16 <= v.prefixlen <= 29:
            raise ValueError("segment prefix length must be between /16 and /29")
        return v


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


class Router(_Strict):
    os: str | None = None
    cores: int = Field(1, ge=1, le=8)
    memory: int = Field(1024, ge=512, le=8192)
    disk: int = Field(10, ge=8, le=100)


class Rule(_Strict):
    from_: str = Field(alias="from")
    to: str
    proto: Literal["tcp", "udp", "icmp", "any"] = "tcp"
    ports: list[int | str] = Field(default_factory=list)
    description: str = ""

    @field_validator("ports")
    @classmethod
    def _ports(cls, v: list) -> list:
        out = []
        for p in v:
            s = str(p)
            m = re.fullmatch(r"(\d{1,5})(?:-(\d{1,5}))?", s)
            if not m:
                raise ValueError(f"invalid port '{s}' (use 443 or 8000-8100)")
            lo, hi = int(m.group(1)), int(m.group(2) or m.group(1))
            if not (1 <= lo <= hi <= 65535):
                raise ValueError(f"port out of range: '{s}'")
            out.append(lo if lo == hi else f"{lo}-{hi}")
        return out


class Test(_Strict):
    name: str | None = None
    from_: str = Field(alias="from")
    to: str
    proto: Literal["tcp", "icmp"] = "tcp"
    port: int | None = Field(None, ge=1, le=65535)
    expect: Literal["open", "closed"]


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
    def _net(cls, v: ipaddress.IPv4Network) -> ipaddress.IPv4Network:
        if not v.is_private or not 16 <= v.prefixlen <= 29:
            raise ValueError("the tunnel network must be private address space between /16 and /29")
        return v

    @field_validator("peers")
    @classmethod
    def _peers(cls, v: list[str]) -> list[str]:
        for name in v:
            if not re.fullmatch(r"[a-z][a-z0-9-]{0,30}", name):
                raise ValueError(f"peer name '{name}' must be lowercase letters, digits or dashes")
        if len(set(v)) != len(v):
            raise ValueError("peer names must be unique")
        return v


class Access(_Strict):
    wireguard: WireGuard | None = None


class RangeSpec(_Strict):
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

    @model_validator(mode="after")
    def _cross_checks(self) -> "RangeSpec":
        errs: list[str] = []
        segs = {s.name: s for s in self.segments}
        hosts = {h.name: h for h in self.hosts}
        if len(segs) != len(self.segments):
            errs.append("segment names must be unique")
        if len(hosts) != len(self.hosts):
            errs.append("host names must be unique")
        if ROUTER in hosts:
            errs.append(f"'{ROUTER}' is reserved for the range router (created automatically)")
        if set(segs) & set(hosts):
            errs.append(f"names used for both a segment and a host: {sorted(set(segs) & set(hosts))}")
        vlans = [s.vlan for s in self.segments]
        if len(set(vlans)) != len(vlans):
            errs.append("each segment needs its own VLAN")
        for i, a in enumerate(self.segments):
            for b in self.segments[i + 1:]:
                if a.cidr.overlaps(b.cidr):
                    errs.append(f"segments {a.name} and {b.name} overlap ({a.cidr} / {b.cidr})")
        seen_addr: dict = {}
        for h in self.hosts:
            seg = segs.get(h.segment)
            if not seg:
                errs.append(f"host {h.name}: unknown segment '{h.segment}'")
                continue
            if h.address not in seg.cidr or h.address in (seg.cidr.network_address, seg.cidr.broadcast_address):
                errs.append(f"host {h.name}: {h.address} is not a usable address in {seg.cidr}")
            if h.address == seg.gateway:
                errs.append(f"host {h.name}: {h.address} is the router's address in segment {seg.name}")
            if h.address in seen_addr:
                errs.append(f"hosts {seen_addr[h.address]} and {h.name} share {h.address}")
            seen_addr[h.address] = h.name
        endpoints = set(segs) | set(hosts)
        for i, r in enumerate(self.policy):
            for end in (r.from_, r.to):
                if end not in endpoints:
                    errs.append(f"policy[{i}]: unknown segment or host '{end}'")
            if r.proto in ("icmp", "any") and r.ports:
                errs.append(f"policy[{i}]: ports only apply to tcp/udp")
            if r.proto in ("tcp", "udp") and not r.ports:
                errs.append(f"policy[{i}]: tcp/udp rules need at least one port")
            sf, st = self.segment_of(r.from_), self.segment_of(r.to)
            if sf and st and sf == st:
                errs.append(f"policy[{i}]: {r.from_} and {r.to} are in the same segment ({sf}); "
                            "traffic inside a segment never reaches the router, so it cannot be filtered")
        wg = self.access.wireguard if self.access else None
        if wg:
            for s in wg.reach:
                if s not in segs:
                    errs.append(f"access.wireguard.reach: unknown segment '{s}'")
            for s in self.segments:
                if s.cidr.overlaps(wg.network):
                    errs.append(f"access.wireguard.network {wg.network} overlaps segment {s.name} ({s.cidr})")
            if len(wg.peers) > wg.network.num_addresses - 3:
                errs.append(f"access.wireguard.network {wg.network} is too small for {len(wg.peers)} peers")
        for i, t in enumerate(self.tests):
            if t.from_ not in hosts:
                errs.append(f"tests[{i}]: 'from' must be a host, got '{t.from_}'")
            if t.to not in hosts and t.to != "internet":
                try:
                    ipaddress.IPv4Address(t.to)
                except ValueError:
                    errs.append(f"tests[{i}]: 'to' must be a host, an IPv4 address or 'internet'")
            if t.proto == "tcp" and t.port is None and t.to != "internet":
                errs.append(f"tests[{i}]: tcp tests need a port")
        if errs:
            raise ValueError("; ".join(errs))
        return self

    # ------------------------------------------------------------------ helpers
    def segment(self, name: str) -> Segment:
        return next(s for s in self.segments if s.name == name)

    def host(self, name: str) -> Host:
        return next(h for h in self.hosts if h.name == name)

    def segment_of(self, endpoint: str) -> str | None:
        if any(s.name == endpoint for s in self.segments):
            return endpoint
        for h in self.hosts:
            if h.name == endpoint:
                return h.segment
        return None

    def endpoint_cidr(self, endpoint: str) -> str:
        for s in self.segments:
            if s.name == endpoint:
                return str(s.cidr)
        return f"{self.host(endpoint).address}/32"

    def hosts_in(self, segment: str) -> list[Host]:
        return [h for h in self.hosts if h.segment == segment]


def normalize(spec: RangeSpec, default_os: str) -> RangeSpec:
    """Fill defaults that depend on engine configuration (OS)."""
    data = spec.model_dump(by_alias=True)
    if not data["router"].get("os"):
        data["router"]["os"] = default_os
    for h in data["hosts"]:
        if not h.get("os"):
            h["os"] = default_os
    return RangeSpec.model_validate(data)


def canonical(spec: RangeSpec) -> str:
    data = spec.model_dump(mode="json", by_alias=True)
    if data.get("access") is None:              # added later: leaving it out keeps older specs' hashes unchanged
        data.pop("access", None)
    for h in data["hosts"]:
        if h.get("iso") is None:
            h.pop("iso", None)
        if h.get("install") == "template":
            h.pop("install", None)
        if h.get("nested") is False:
            h.pop("nested", None)
    return json.dumps(data, sort_keys=True, separators=(",", ":"))


def spec_hash(spec: RangeSpec) -> str:
    return hashlib.sha256(canonical(spec).encode()).hexdigest()


def resolve_spec_path(path: str | Path, ranges_dir: Path) -> Path:
    p = Path(path)
    if not p.is_absolute():
        cand = ranges_dir / p
        p = cand if cand.exists() or not (Path.cwd() / p).exists() else Path.cwd() / p
    try:
        p = p.resolve(strict=True)
    except FileNotFoundError:
        raise SpecError(f"spec file not found: {path}", hint=f"Specs live in {ranges_dir}/<name>.yaml")
    if not p.is_relative_to(ranges_dir.resolve()):
        raise SpecError(f"spec files must be inside {ranges_dir}", hint="Write the spec to ranges/<name>.yaml")
    if p.suffix not in (".yaml", ".yml") or not p.is_file():
        raise SpecError("spec must be a .yaml file")
    if p.stat().st_size > MAX_SPEC_BYTES:
        raise SpecError("spec file is too large")
    return p


def parse_spec(text: str) -> RangeSpec:
    """Parse YAML text. Error messages never echo file content (the engine can read secrets)."""
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        mark = getattr(e, "problem_mark", None)
        where = f" at line {mark.line + 1}, column {mark.column + 1}" if mark else ""
        raise SpecError(f"YAML syntax error{where}: {getattr(e, 'problem', None) or 'invalid YAML'}")
    if not isinstance(data, dict):
        raise SpecError("spec must be a YAML mapping (apiVersion, name, segments, hosts, ...)")
    try:
        return RangeSpec.model_validate(data)
    except ValidationError as e:
        errors = []
        for err in e.errors(include_url=False, include_input=False):
            loc = ".".join(str(x) for x in err["loc"]) or "(spec)"
            msg = err["msg"].removeprefix("Value error, ")
            errors.append({"location": loc, "message": msg})
        raise SpecError(f"spec has {len(errors)} problem(s)", errors)


def load_spec(path: str | Path, ranges_dir: Path, default_os: str) -> tuple[RangeSpec, Path]:
    p = resolve_spec_path(path, ranges_dir)
    return normalize(parse_spec(p.read_text()), default_os), p


# ---------------------------------------------------------------------- tests
def effective_tests(spec: RangeSpec, probe: tuple[str, int] = INTERNET_PROBE) -> list[dict]:
    """Explicit tests plus, with auto_tests, tests derived from the policy and egress settings.
    probe: the public host:port that stands for "the internet" (configurable per installation)."""
    out: list[dict] = []

    def add(name, src, dst, proto, port, expect, origin):
        key = (src, dst, proto, port)
        if any((t["from"], t["to"], t["proto"], t["port"]) == key for t in out):
            return
        out.append({"name": name, "from": src, "to": dst, "proto": proto, "port": port,
                    "expect": expect, "origin": origin})

    for t in spec.tests:
        port = t.port
        if t.to == "internet" and t.proto == "tcp":
            port = port or probe[1]
        add(t.name or f"{t.from_} -> {t.to} {t.proto}{'/' + str(port) if port else ''} {t.expect}",
            t.from_, t.to, t.proto, port, t.expect, "spec")
    if not spec.auto_tests:
        return out

    def first_host(endpoint: str):
        if any(h.name == endpoint for h in spec.hosts):
            return endpoint
        hs = spec.hosts_in(endpoint)
        return hs[0].name if hs else None

    allowed_pairs: dict[tuple[str, str], set] = {}
    for r in spec.policy:
        sf, st = spec.segment_of(r.from_), spec.segment_of(r.to)
        allowed_pairs.setdefault((sf, st), set())
        if r.proto == "any":
            allowed_pairs[(sf, st)].add("any")
        for p in r.ports:
            allowed_pairs[(sf, st)].add(p)
        src, dst = first_host(r.from_), first_host(r.to)
        if not src or not dst:
            continue
        if r.proto == "tcp" and r.ports:
            p = r.ports[0]
            port = int(str(p).split("-")[0])
            add(f"policy allows {src} -> {dst} tcp/{port}", src, dst, "tcp", port, "open", "policy")
        elif r.proto == "icmp":
            add(f"policy allows {src} -> {dst} icmp", src, dst, "icmp", None, "open", "policy")

    for a in spec.segments:
        for b in spec.segments:
            if a.name == b.name:
                continue
            src, dst = first_host(a.name), first_host(b.name)
            if not src or not dst:
                continue
            allowed = allowed_pairs.get((a.name, b.name), set())
            if "any" in allowed or 22 in allowed:
                continue
            add(f"isolation: {src} ({a.name}) -> {dst} ({b.name}) tcp/22 blocked",
                src, dst, "tcp", 22, "closed", "isolation")

    for s in spec.segments:
        src = first_host(s.name)
        if not src:
            continue
        add(f"egress: {s.name} internet {'allowed' if s.internet else 'blocked'}",
            src, "internet", "tcp", probe[1], "open" if s.internet else "closed", "egress")
    return out
