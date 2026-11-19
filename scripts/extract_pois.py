#!/usr/bin/env python
"""Extract four small Gauteng POI datasets from the Geofabrik extract.

Outputs GeoJSON in ../gauteng-poi-atlas/data:
  complexes.geojson (30)  hotels.geojson (30)
  health.geojson    (60)  commerce.geojson (60, physical + online channels)
  all.geojson, stats.json
"""

from __future__ import annotations

import json
import math
import re
from array import array
from pathlib import Path

import numpy as np
import osmium

ROOT = Path(__file__).resolve().parent.parent
PBF = ROOT.parent / "gauteng-map" / "data" / "south-africa-and-lesotho-latest.osm.pbf"
PROVINCE = ROOT.parent / "gauteng-map" / "data" / "geoBoundaries-ZAF-ADM1.geojson"
OUT = ROOT / "data"

GAUTENG = (27.10, -27.00, 29.20, -25.10)

HEALTH_AMENITIES = {"hospital", "clinic", "doctors", "dentist", "pharmacy"}
LODGING = {"hotel"}
COMMERCE_SHOPS = {
    "supermarket", "department_store", "mall", "convenience", "clothes", "electronics",
    "computer", "furniture", "hardware", "doityourself", "sports", "shoes", "books",
    "toys", "jewelry", "cosmetics", "chemist", "variety_store", "wholesale",
}
CHAIN_BRANDS = {
    "checkers", "checkers hyper", "shoprite", "pick n pay", "picknpay", "woolworths",
    "spar", "clicks", "dis-chem", "dischem", "game", "makro", "builders warehouse",
    "pep", "mr price", "mr price home", "foschini", "total sports", "sportsmans warehouse",
    "decathlon", "leroy merlin", "incredible connection", "hifi corp", "torga optical",
    "sheet street", "jet", "ackermans", "cash crusaders", "bargain books",
}
COMPLEX_NAME = re.compile(
    r"\b(estate|complex|apartments?|flats?|mews|residences?|lofts?|townhouses?|"
    r"townhouse|villas?|strand|heights)\b",
    re.IGNORECASE,
)
COMPLEX_EXCLUDE = re.compile(
    r"\b(close|street|st\.?|road|rd\.?|avenue|ave\.?|laan|drive|crescent|court|"
    r"sport(s)?|stadium|school|primary|secondary|college|university|church|chapel|"
    r"mosque|cemetery|wedding|conference|events?|plaza|mall|centre|center|station|"
    r"parking|depot|factory|works|hospital|clinic)\b",
    re.IGNORECASE,
)

KEEP_TAGS = (
    "name", "amenity", "tourism", "shop", "landuse", "building", "residential",
    "brand", "operator", "website", "contact:website", "phone", "contact:phone",
    "opening_hours", "addr:housenumber", "addr:street", "addr:suburb", "addr:city",
    "addr:postcode",
)


def in_gauteng(lon: float, lat: float) -> bool:
    return GAUTENG[0] <= lon <= GAUTENG[2] and GAUTENG[1] <= lat <= GAUTENG[3]


def load_province():
    """Prepared Gauteng polygon so North West / Mpumalanga fringes are excluded."""
    from shapely.geometry import Point, shape
    from shapely.ops import unary_union
    from shapely.prepared import prep

    payload = json.loads(PROVINCE.read_text(encoding="utf-8"))
    feats = [
        f
        for f in payload["features"]
        if (f["properties"].get("shapeName") or "").strip().lower() == "gauteng"
    ]
    geom = unary_union([shape(f["geometry"]) for f in feats])
    prepared = prep(geom)

    def inside(lon: float, lat: float) -> bool:
        return prepared.contains(Point(lon, lat))

    return inside


def address(tags) -> str:
    parts = [
        " ".join(filter(None, [tags.get("addr:housenumber"), tags.get("addr:street")])),
        tags.get("addr:suburb"),
        tags.get("addr:city"),
        tags.get("addr:postcode"),
    ]
    return ", ".join(p.strip() for p in parts if p and p.strip())


def website(tags) -> str | None:
    for key in ("website", "contact:website", "brand:website", "operator:website"):
        value = tags.get(key)
        if value:
            return value.strip()
    return None


class NodePass(osmium.SimpleHandler):
    def __init__(self) -> None:
        super().__init__()
        self.ids = array("q")
        self.lats = array("d")
        self.lons = array("d")
        self.candidates: list[dict] = []

    def node(self, n: osmium.osm.Node) -> None:
        if not n.location.valid():
            return
        lon, lat = n.location.lon, n.location.lat
        if not in_gauteng(lon, lat):
            return
        self.ids.append(n.id)
        self.lats.append(lat)
        self.lons.append(lon)
        tags = n.tags
        if tags and (tags.get("amenity") or tags.get("tourism") or tags.get("shop") or tags.get("name")):
            kept = {k: tags.get(k) for k in KEEP_TAGS if tags.get(k)}
            if kept:
                self.candidates.append(
                    {"tags": kept, "lat": lat, "lon": lon, "osm_type": "node", "osm_id": n.id}
                )


class WayPass(osmium.SimpleHandler):
    def __init__(self, ids: np.ndarray, lats: np.ndarray, lons: np.ndarray) -> None:
        super().__init__()
        self.ids, self.lats, self.lons = ids, lats, lons
        self.candidates: list[dict] = []

    def _centroid(self, w: osmium.osm.Way):
        refs = np.fromiter((nd.ref for nd in w.nodes), dtype=np.int64)
        if refs.size == 0:
            return None
        pos = np.clip(np.searchsorted(self.ids, refs), 0, len(self.ids) - 1)
        hit = self.ids[pos] == refs
        if not hit.any():
            return None
        return float(self.lons[pos][hit].mean()), float(self.lats[pos][hit].mean())

    def way(self, w: osmium.osm.Way) -> None:
        tags = w.tags
        if not tags:
            return
        if not (
            tags.get("amenity") or tags.get("tourism") or tags.get("shop")
            or tags.get("landuse") or tags.get("building") or tags.get("name")
        ):
            return
        centre = self._centroid(w)
        if centre is None:
            return
        lon, lat = centre
        if not in_gauteng(lon, lat):
            return
        kept = {k: tags.get(k) for k in KEEP_TAGS if tags.get(k)}
        if not kept:
            return
        self.candidates.append(
            {"tags": kept, "lat": lat, "lon": lon, "osm_type": "way", "osm_id": w.id}
        )


def classify(c: dict) -> set[str]:
    t = c["tags"]
    kinds: set[str] = set()
    name = (t.get("name") or "").strip()

    if t.get("tourism") in LODGING:
        kinds.add("hotel")
    if t.get("amenity") in HEALTH_AMENITIES:
        kinds.add("health")
    brand = (t.get("brand") or "").strip().lower()
    lowered = name.lower()
    chain_hit = brand in CHAIN_BRANDS
    name_hit = any(
        lowered == chain or lowered.startswith(chain + " ") or lowered.startswith(chain + "-")
        for chain in CHAIN_BRANDS
    )
    if chain_hit or (name_hit and t.get("shop") in COMMERCE_SHOPS):
        kinds.add("commerce")

    # residential complexes / estates: areas only, never streets or civic buildings
    if name and not t.get("amenity") and not t.get("shop") and not t.get("tourism"):
        if not COMPLEX_EXCLUDE.search(name):
            strong = (
                t.get("building") in {"apartments", "residential"}
                or t.get("residential") in {"apartments", "gated", "complex", "estate"}
            )
            weak = bool(COMPLEX_NAME.search(name)) and t.get("landuse") == "residential"
            if (strong or weak) and c["osm_type"] == "way":
                kinds.add("complex")
    return kinds


def even_pick(items: list[dict], limit: int) -> list[dict]:
    """Pick `limit` items evenly spaced across a spatially sorted list."""
    if len(items) <= limit:
        return list(items)
    step = (len(items) - 1) / max(limit - 1, 1)
    return [items[int(round(i * step))] for i in range(limit)]


def balanced_sample(
    pool: list[dict], limit: int, keyfunc=None, seed: int = 7
) -> list[dict]:
    """Spread across the province and across brands/subtypes."""
    pool = sorted(pool, key=lambda i: (i["lat"], i["lon"]))
    if keyfunc is None:
        return even_pick(pool, limit)

    import random

    rng = random.Random(seed)
    groups: dict[str, list[dict]] = {}
    for item in pool:
        groups.setdefault(keyfunc(item) or "other", []).append(item)
    queues: dict[str, list[dict]] = {}
    for key, values in groups.items():
        shuffled = list(values)
        rng.shuffle(shuffled)
        queues[key] = shuffled
    keys = sorted(queues)
    out: list[dict] = []
    while len(out) < limit and any(queues.values()):
        for key in keys:
            if queues[key] and len(out) < limit:
                out.append(queues[key].pop(0))
    out.sort(key=lambda i: (i["lat"], i["lon"]))
    return out


def to_feature(c: dict, kind: str) -> dict:
    t = c["tags"]
    props = {
        "name": (t.get("name") or "").strip(),
        "category": kind,
        "subtype": t.get("tourism") or t.get("amenity") or t.get("shop")
        or t.get("building") or t.get("landuse") or "poi",
        "brand": t.get("brand"),
        "address": address(t) or None,
        "website": website(t),
        "phone": t.get("phone") or t.get("contact:phone"),
        "opening_hours": t.get("opening_hours"),
        "osm_type": c["osm_type"],
        "osm_id": c["osm_id"],
    }
    if kind == "commerce":
        props["channel"] = "physical + online" if props["website"] else "physical"
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [round(c["lon"], 6), round(c["lat"], 6)]},
        "properties": props,
    }


GENERIC_NAMES = {
    "surgery", "clinic", "pharmacy", "hospital", "doctors", "doctor", "medical centre",
    "medical center", "hotel", "guest house", "complex", "estate", "apartments",
    "residential", "shop", "store", "supermarket",
}
BAD_NAME = re.compile(r"\b(hostel|garage|garages|parking|toilets?|entrance|boom)\b", re.IGNORECASE)
HAS_WORD = re.compile(r"[A-Za-z]{3,}")


def name_is_good(name: str) -> bool:
    name = (name or "").strip()
    if len(name) < 4 or name.lower() in GENERIC_NAMES:
        return False
    if not HAS_WORD.search(name) or BAD_NAME.search(name):
        return False
    return True


def dedupe_by_name(pool: list[dict]) -> list[dict]:
    seen: set[str] = set()
    out: list[dict] = []
    for item in pool:
        key = re.sub(r"[^a-z0-9]", "", item["tags"]["name"].lower())
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def main() -> int:
    if not PBF.exists():
        raise SystemExit(f"missing {PBF}")
    OUT.mkdir(parents=True, exist_ok=True)

    nodes = NodePass()
    nodes.apply_file(str(PBF))
    ids = np.frombuffer(nodes.ids, dtype=np.int64)
    lats = np.frombuffer(nodes.lats, dtype=np.float64)
    lons = np.frombuffer(nodes.lons, dtype=np.float64)
    order = np.argsort(ids, kind="stable")
    ids, lats, lons = ids[order], lats[order], lons[order]
    print(f"nodes indexed: {len(ids):,}")

    ways = WayPass(ids, lats, lons)
    ways.apply_file(str(PBF))

    pools: dict[str, list[dict]] = {"complex": [], "hotel": [], "health": [], "commerce": []}
    inside = load_province()
    dropped = 0
    for candidate in nodes.candidates + ways.candidates:
        if not inside(candidate["lon"], candidate["lat"]):
            dropped += 1
            continue
        for kind in classify(candidate):
            pools[kind].append(candidate)
    print(f"candidates outside the Gauteng polygon dropped: {dropped:,}")

    targets = {"complex": 30, "hotel": 30, "health": 60, "commerce": 60}
    files = {
        "complex": "complexes.geojson",
        "hotel": "hotels.geojson",
        "health": "health.geojson",
        "commerce": "commerce.geojson",
    }

    def commerce_key(c: dict) -> str:
        t = c["tags"]
        return (t.get("brand") or (t.get("name") or "").split(" ")[0]).strip()

    def health_key(c: dict) -> str:
        return c["tags"].get("amenity", "other")

    keys = {
        "complex": None,
        "hotel": None,
        "health": health_key,
        "commerce": commerce_key,
    }

    stats: dict = {}
    all_features: list[dict] = []

    for kind, limit in targets.items():
        pool = [c for c in pools[kind] if name_is_good(c["tags"].get("name", ""))]
        if kind == "health":
            pool = dedupe_by_name(pool)
        sample = balanced_sample(pool, limit, keys[kind])
        features = [to_feature(c, kind) for c in sample]
        collection = {
            "type": "FeatureCollection",
            "name": f"gauteng-{kind}",
            "metadata": {
                "source": "OpenStreetMap (Geofabrik south-africa-and-lesotho extract)",
                "license": "ODbL 1.0",
                "bbox": list(GAUTENG),
                "count": len(features),
                "candidates": len(pool),
            },
            "features": features,
        }
        (OUT / files[kind]).write_text(
            json.dumps(collection, indent=1, ensure_ascii=False), encoding="utf-8"
        )
        all_features.extend(features)
        stats[kind] = {"emitted": len(features), "candidates": len(pool)}
        print(f"{kind:9s} emitted {len(features):3d} of {len(pool):5d} candidates")

    (OUT / "all.geojson").write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "name": "gauteng-poi-atlas",
                "metadata": {
                    "source": "OpenStreetMap via Geofabrik, 2026-11-17 extract",
                    "license": "ODbL 1.0",
                    "generated": "2026-11-18",
                },
                "features": all_features,
            },
            indent=1,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (OUT / "stats.json").write_text(json.dumps(stats, indent=2), encoding="utf-8")

    # script-tag payload so index.html also works from file:// (fetch is blocked there)
    points = [
        {
            "kind": f["properties"]["category"],
            "lat": f["geometry"]["coordinates"][1],
            "lon": f["geometry"]["coordinates"][0],
            "props": f["properties"],
        }
        for f in all_features
    ]
    (OUT / "points.js").write_text(
        "window.POI_DATA=" + json.dumps(points, ensure_ascii=False, separators=(",", ":")) + ";\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
