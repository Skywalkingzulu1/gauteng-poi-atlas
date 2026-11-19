# Gauteng POI Atlas

Small, open datasets for Gauteng province, South Africa — mapped in the browser.

Four categories, sampled from OpenStreetMap and clipped to the real Gauteng boundary:

| dataset | points | contents |
| --- | --- | --- |
| `data/complexes.geojson` | 30 | residential complexes / gated estates / apartment blocks |
| `data/hotels.geojson` | 30 | `tourism=hotel` |
| `data/health.geojson` | 60 | hospitals, clinics, doctors, dentists, pharmacies |
| `data/commerce.geojson` | 60 | retail chains (Checkers, Shoprite, Pick n Pay, Woolworths, Spar, Clicks, PEP, ...) with `channel` = physical / physical + online |

`data/all.geojson` merges all 180 points; `data/stats.json` records how many candidates each
sample was drawn from.

Open `index.html` (or the GitHub Pages site) for the map: filter by category, search names,
brands and suburbs, click a point for address, phone, website and the OSM object.

## How the data was produced

Source: `south-africa-and-lesotho-latest.osm.pbf` from
[Geofabrik](https://download.geofabrik.de/africa/south-africa-and-lesotho.html), extracted
2026-11-17 (524.8 MB).

1. Two streaming passes with [pyosmium](https://osmcode.org/pyosmium/) collect every POI node,
   and a centroid for every tagged way, inside the Gauteng bbox.
2. Candidates outside the province polygon
   ([geoBoundaries](https://www.geoboundaries.org) ZAF ADM1, CC BY 4.0) are dropped — 185,260 of
   them — so the Vaal Triangle, Potchefstroom and Mpumalanga fringes do not leak in.
3. Category rules: `tourism=hotel`; `amenity` in hospital/clinic/doctors/dentist/pharmacy;
   brand or name match against a retail chain list with a retail `shop` type; named residential
   areas with `building=apartments|residential`, `residential=apartments|gated|complex|estate`
   or `landuse=residential` plus an estate/complex-style name, excluding streets, schools, sports
   grounds and event venues.
4. Sampling: 30/30/60/60 chosen for geographic spread across the province, and for commerce and
   health also round-robin across brands and facility types, so no single chain or clinic type
   dominates.

Regenerate (needs the PBF from the sibling `gauteng-map` project):

```powershell
python scripts\extract_pois.py
```

Dependencies: `osmium`, `numpy`, `shapely`.

## Data licence and attribution

Data is derived from OpenStreetMap and is © OpenStreetMap contributors, available under the
[Open Database License (ODbL) 1.0](https://opendatacommons.org/licenses/odbl/1-0/).
If you use these files, keep the attribution and share derivative databases alike.
The Gauteng polygon comes from geoBoundaries (CC BY 4.0). See `DATA-LICENSE.md`.

Code in this repository is MIT licensed.
