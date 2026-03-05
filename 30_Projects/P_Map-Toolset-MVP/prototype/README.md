# P_Map-Toolset-MVP Prototype (v0)

This prototype contains a minimal converter between:

- `simple_map_v0`
- `canonical_lane_graph_v0`

## Convert to canonical lane graph

```bash
python3 convert_map_format.py \
  --input examples/simple_highway_segment_v0.json \
  --to-format canonical \
  --out /tmp/canonical_lane_graph.json
```

## Convert back to simple map

```bash
python3 convert_map_format.py \
  --input /tmp/canonical_lane_graph.json \
  --to-format simple \
  --out /tmp/simple_map_roundtrip.json
```

## Validate canonical lane graph

```bash
python3 validate_canonical_map.py \
  --map /tmp/canonical_lane_graph.json \
  --report-out /tmp/canonical_lane_graph.validation.json
```

## Compute route on canonical lane graph

```bash
python3 compute_canonical_route.py \
  --map /tmp/canonical_lane_graph.json \
  --cost-mode length \
  --report-out /tmp/canonical_lane_graph.route.json
```
