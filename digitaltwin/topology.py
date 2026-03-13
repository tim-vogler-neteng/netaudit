"""Parse ContainerLab topology YAML and derive VM sizing."""

from __future__ import annotations

import yaml
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# RAM (GB) per node kind — add new kinds here as needed
_RAM_PER_NODE: dict[str, float] = {
    "srl": 2.0,
    "ceos": 2.0,
    "crpd": 1.0,
    "xrd": 2.0,
    "frr": 0.5,
    "linux": 0.5,
}
_RAM_DEFAULT = 1.5
_HOST_OVERHEAD_GB = 2.0

# DigitalOcean droplet sizes ordered smallest → largest.
# DO deprecated generic slugs (e.g. s-4vcpu-8gb) in favour of vendor-specific ones.
# Using AMD variants; override with --size if you prefer Intel.
_DROPLET_SIZES = [
    {"slug": "s-2vcpu-4gb-amd",   "vcpu": 2,  "ram": 4},
    {"slug": "s-4vcpu-8gb-amd",   "vcpu": 4,  "ram": 8},
    {"slug": "s-8vcpu-16gb-amd",  "vcpu": 8,  "ram": 16},
    {"slug": "s-8vcpu-32gb-amd",  "vcpu": 8,  "ram": 32},
    {"slug": "m-8vcpu-64gb",      "vcpu": 8,  "ram": 64},
]


@dataclass
class NodeInfo:
    name: str
    kind: str
    image: str


@dataclass
class Topology:
    name: str
    nodes: list[NodeInfo]
    links: list[tuple[str, str]]  # (node_a, node_b)
    raw: dict[str, Any] = field(repr=False)
    source_path: Path = field(repr=False)

    @property
    def node_count(self) -> int:
        return len(self.nodes)

    def required_ram_gb(self) -> float:
        total = _HOST_OVERHEAD_GB
        for node in self.nodes:
            total += _RAM_PER_NODE.get(node.kind, _RAM_DEFAULT)
        return total

    def droplet_size(self) -> str:
        ram_needed = self.required_ram_gb()
        for size in _DROPLET_SIZES:
            if size["ram"] >= ram_needed:
                return size["slug"]
        return _DROPLET_SIZES[-1]["slug"]

    def node_pairs(self) -> list[tuple[str, str]]:
        """Unique node pairs derived from link endpoints for ping tests."""
        pairs: set[frozenset[str]] = set()
        for a, b in self.links:
            pairs.add(frozenset([a, b]))
        return [(sorted(p)[0], sorted(p)[1]) for p in pairs]


def load(path: str | Path) -> Topology:
    path = Path(path)
    with path.open() as f:
        raw = yaml.safe_load(f)

    topo_section = raw.get("topology", {})
    nodes_raw = topo_section.get("nodes", {})
    links_raw = topo_section.get("links", [])

    # Resolve default kind / image from topology-level defaults if present
    defaults = topo_section.get("defaults", {})
    default_kind = defaults.get("kind", "")
    default_image = defaults.get("image", "")

    nodes = []
    for name, cfg in nodes_raw.items():
        cfg = cfg or {}
        kind = cfg.get("kind", default_kind)
        image = cfg.get("image", default_image)
        nodes.append(NodeInfo(name=name, kind=kind, image=image))

    links = []
    for link in links_raw:
        endpoints = link.get("endpoints", [])
        if len(endpoints) == 2:
            node_a = endpoints[0].split(":")[0]
            node_b = endpoints[1].split(":")[0]
            links.append((node_a, node_b))

    return Topology(
        name=raw.get("name", path.stem),
        nodes=nodes,
        links=links,
        raw=raw,
        source_path=path,
    )
