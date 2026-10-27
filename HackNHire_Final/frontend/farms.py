"""farms.py - farms from the map or from a file -> the backend's Farm objects (common.Farm)."""

from backend import common

SUPPORTED = ("Point", "Polygon", "MultiPolygon")


def _skip_reason(geometry):
    return f"unsupported shape: {geometry.get('type') or 'no geometry'}"


def prepare_farms(features, grid, point_mode="pixel"):
    """(farms, keys, skipped): one common.Farm per usable feature, the matching feature keys
    (the map's ids; the position when there is no id), and [(name, reason)] for features that
    can't be checked. plot_id = the farm's name, made unique the way the backend does
    (Name, Name-2, ...)."""
    farms, keys, skipped, seen = [], [], [], {}
    for i, feature in enumerate(features):
        props = dict(feature.get("properties") or {})
        name = str(props.get("name") or props.get("plot_id") or props.get("id") or f"farm {i + 1}")
        geometry = feature.get("geometry") or {}
        if geometry.get("type") not in SUPPORTED:
            skipped.append((name, _skip_reason(geometry)))
            continue
        seen[name] = seen.get(name, 0) + 1
        props["plot_id"] = name if seen[name] == 1 else f"{name}-{seen[name]}"
        made = common.farms_from_features([{"type": "Feature", "properties": props, "geometry": geometry}],
                                          grid, point_mode)
        if not made:
            skipped.append((name, "the outline could not be repaired"))
            continue
        farms.append(made[0])
        keys.append(props.get("id", i))
    return farms, keys, skipped


def farm_area_ha(farm, grid):
    return 0.0 if farm.kind == "point" else common.area_m2(farm.geom, grid) / 10_000


def features_from_file(data):
    """A loaded GeoJSON file -> (features to put on the map, [(name, reason)] skipped).
    Accepts a FeatureCollection, a Feature or a bare geometry, like common.load_farms."""
    if data.get("type") == "FeatureCollection":
        features = data.get("features") or []
    elif data.get("type") == "Feature":
        features = [data]
    else:
        features = [{"type": "Feature", "properties": {}, "geometry": data}]
    kept, skipped = [], []
    for i, feature in enumerate(features):
        props = dict(feature.get("properties") or {})
        name = str(props.get("plot_id") or props.get("name") or props.get("id") or feature.get("id")
                   or f"farm {i + 1}")
        geometry = feature.get("geometry") or {}
        if geometry.get("type") not in SUPPORTED:
            skipped.append((name, _skip_reason(geometry)))
            continue
        props["name"] = name
        props.pop("id", None)  # the map gives every farm its own id
        kept.append({"type": "Feature", "properties": props, "geometry": geometry})
    return kept, skipped
