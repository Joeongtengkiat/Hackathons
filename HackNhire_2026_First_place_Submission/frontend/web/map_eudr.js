// map_eudr.js - the colour-coded EUDR tile layer. Tiles come from Python (eudr://tiles/...), drawn
// from the stencils stored in Neon. The legend and the loading banner are Qt widgets over the map.
addCss('.eudr-tiles img { image-rendering: pixelated; }');

var eudr = { layer: null, hidden: [], opacity: 0.7, visible: true, key: '' };

// The map's schema is in every tile URL, so a tile cached for one map is never shown for another.
function tileUrl() {
  return 'eudr://tiles/{z}/{x}/{y}.png?map=' + encodeURIComponent(eudr.key) + '&hide=' + eudr.hidden.join(',');
}

function setMapInfo(info) {
  eudr.key = info.schema || info.map_version;
  bounds = info.bounds;  // lock the view to the map stored in Neon
  map.setMaxBounds(bounds);
  aoi.setBounds(bounds);
  lockView(true);
  if (eudr.layer) map.removeLayer(eudr.layer);
  eudr.layer = L.tileLayer(tileUrl(), {
    bounds: bounds, opacity: eudr.opacity, maxZoom: 19, zIndex: 5,
    className: 'eudr-tiles', keepBuffer: 4
  });
  if (eudr.visible) eudr.layer.addTo(map);
}

// No map to show (none stored yet, or the one shown was deleted).
function clearMapInfo() {
  if (eudr.layer) map.removeLayer(eudr.layer);
  eudr.layer = null;
  setProtectedAreas(null);
}

function setHiddenClasses(codes) {
  eudr.hidden = codes.slice();
  if (eudr.layer) eudr.layer.setUrl(tileUrl());
}
function setOverlayOpacity(value) { eudr.opacity = value; if (eudr.layer) eudr.layer.setOpacity(value); }
function setOverlayVisible(on) {
  eudr.visible = on;
  if (!eudr.layer) return;
  if (on) eudr.layer.addTo(map); else map.removeLayer(eudr.layer);
}

// Protected areas (WDPA polygons stored with the map, protected_areas.py): a dashed outline in
// their own pane, above the EUDR tiles and below the farms, with the area's name on hover.
map.createPane('protected').style.zIndex = 350;
var protectedAreas = { layer: null, visible: true };

function protectedTooltip(p) {
  var lines = ['<b>' + esc(p.name || 'Protected area') + '</b>'];
  if (p.designation) lines.push(esc(p.designation));
  if (p.iucn_cat) lines.push('IUCN category ' + esc(p.iucn_cat));
  if (p.wdpa_id) lines.push('WDPA ' + esc(p.wdpa_id));
  return lines.join('<br>');
}

function setProtectedAreas(fc) {
  if (protectedAreas.layer) map.removeLayer(protectedAreas.layer);
  protectedAreas.layer = null;
  if (!fc || !fc.features || !fc.features.length) return;
  var colour = CONFIG.protected.colour;
  protectedAreas.layer = L.geoJSON(fc, {
    pane: 'protected',
    style: function () {
      return { color: colour, weight: 2, opacity: 0.95, dashArray: '6 4', fillColor: colour, fillOpacity: 0.12 };
    },
    onEachFeature: function (feature, layer) {
      layer.bindTooltip(protectedTooltip(feature.properties || {}), { sticky: true });
    }
  });
  if (protectedAreas.visible) protectedAreas.layer.addTo(map);
}

function setProtectedVisible(on) {
  protectedAreas.visible = on;
  if (!protectedAreas.layer) return;
  if (on) protectedAreas.layer.addTo(map); else map.removeLayer(protectedAreas.layer);
}
