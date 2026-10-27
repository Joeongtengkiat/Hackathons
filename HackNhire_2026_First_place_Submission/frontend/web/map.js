// map.js - the map page: Leaflet + leaflet.draw. Farms are polygons and points.
// Python gets every change through bridge.update(GeoJSON) and the drawing state through
// bridge.state(...); anything else goes through send(kind, data).
var bounds = CONFIG.initialBounds;  // replaced by the extent of the map stored in Neon (map_eudr.js)
var COLORS = CONFIG.shapeColours;

function addCss(css) {
  var style = document.createElement('style');
  style.textContent = css;
  document.head.appendChild(style);
}

// Lock the view to the box: no panning outside it, no zooming out past it
var map = L.map('map', {
  zoomSnap: 0, zoomDelta: 0.5, zoomControl: false,
  maxBounds: bounds, maxBoundsViscosity: 1.0
});
L.control.zoom({ position: 'bottomright' }).addTo(map);
L.control.scale({ position: 'bottomleft' }).addTo(map);

// Smallest zoom at which the box still covers the whole map viewport
function lockView(force) {
  var minZoom = map.getBoundsZoom(bounds, true);
  map.setMinZoom(minZoom);
  if (force || !map._loaded || map.getZoom() < minZoom) {
    map.setView(L.latLngBounds(bounds).getCenter(), minZoom, { animate: false });
  }
}
lockView(false);
map.on('resize', function () { lockView(false); });

var basemaps = {
  streets: L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 19, attribution: '&copy; OpenStreetMap contributors'
  }),
  satellite: L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', {
    maxZoom: 19, attribution: 'Tiles &copy; Esri'
  })
};
var basemap = basemaps.satellite.addTo(map);
function setBasemap(name) {
  map.removeLayer(basemap);
  basemap = basemaps[name].addTo(map);
  basemap.bringToBack();
}

// Outline of the area of interest
var aoi = L.rectangle(bounds, { color: '#e63946', weight: 2, dashArray: '6 6', fill: false, interactive: false }).addTo(map);

map.createPane('halos').style.zIndex = 390;  // white outlines under checked farms
var drawnItems = new L.FeatureGroup().addTo(map);
var haloGroup = L.featureGroup().addTo(map);
var vertexIcon = new L.DivIcon({ iconSize: new L.Point(14, 14), className: 'vertex-handle' });
L.Edit.PolyVerticesEdit.prototype.options.icon = vertexIcon;
L.Edit.PolyVerticesEdit.prototype.options.touchIcon = vertexIcon;

function pointIcon(colour, selected) {
  var size = selected ? 18 : 14;
  return L.divIcon({ className: 'point-icon', iconSize: [size, size],
                     html: '<span style="background:' + colour + '"></span>' });
}

var drawHandler = new L.Draw.Polygon(map, {
  allowIntersection: false, showArea: false, guidelineDistance: 12,
  icon: vertexIcon, touchIcon: vertexIcon
});
var pointHandler = new L.Draw.Marker(map, { icon: pointIcon(COLORS[0], true) });
var editHandler = new L.EditToolbar.Edit(map, {
  featureGroup: drawnItems,
  selectedPathOptions: { dashArray: '8, 8', fill: true, fillOpacity: 0.25, maintainColor: true }
});

var bridge = null;
var mode = 'idle';      // idle | drawing | placing | editing
var selectedId = null;
var nextId = 1;
var activeDraw = null;  // the draw handler in use
var verdicts = {};      // id -> {verdict, stale}: the last check, set by Python

function esc(s) { return String(s).replace(/[&<>"']/g, function (c) { return '&#' + c.charCodeAt(0) + ';'; }); }
function send(kind, data) { if (bridge) bridge.send(kind, JSON.stringify(data === undefined ? null : data)); }
function colorFor(id) { return COLORS[(id - 1) % COLORS.length]; }
function isPoint(layer) { return layer instanceof L.Marker; }
function strokeFor(id) {
  var v = verdicts[id];
  if (!v) return colorFor(id);
  return v.stale ? '#9ca3af' : CONFIG.verdicts[v.verdict].colour;
}
function applyStyle(layer, selected) {
  var id = layer.feature.properties.id, v = verdicts[id], c = strokeFor(id);
  if (isPoint(layer)) { layer.setIcon(pointIcon(c, selected)); return; }
  layer.setStyle({
    color: c, fillColor: c, opacity: 1,
    weight: selected ? 4 : (v && !v.stale ? 3 : 2.5),
    dashArray: v && v.stale ? '6 5' : null,
    fillOpacity: selected ? 0.3 : (v ? 0.08 : 0.18)
  });
}
function findLayer(id) {
  return drawnItems.getLayers().filter(function (l) { return l.feature.properties.id === id; })[0];
}
function sortedLayers() {
  return drawnItems.getLayers().sort(function (a, b) { return a.feature.properties.id - b.feature.properties.id; });
}
function layerBounds(layer) {
  return isPoint(layer) ? L.latLngBounds([layer.getLatLng(), layer.getLatLng()]) : layer.getBounds();
}
function labelAnchor(layer) { return isPoint(layer) ? layer.getLatLng() : layer.getCenter(); }

// Geodesic area of a polygon layer in km2: every part, outer rings minus holes
function ringKm2(latlngs) { return L.GeometryUtil.geodesicArea(latlngs) / 1e6; }
function polygonsOf(layer) {
  var g = layer.toGeoJSON().geometry;
  return g.type === 'Polygon' ? [g.coordinates] : (g.type === 'MultiPolygon' ? g.coordinates : []);
}
function areaKm2(layer) {
  var km2 = 0;
  polygonsOf(layer).forEach(function (rings) {
    rings.forEach(function (ring, i) {
      var a = ringKm2(ring.map(function (c) { return L.latLng(c[1], c[0]); }));
      km2 += i === 0 ? a : -a;
    });
  });
  return km2;
}
function vertexCount(layer) {
  var n = 0;
  polygonsOf(layer).forEach(function (rings) { rings.forEach(function (ring) { n += ring.length - 1; }); });
  return n;
}
function drawPoints() { return drawHandler._markers ? drawHandler._markers.length : 0; }

// ---------- state -> Python ----------
function sync() {
  var features = sortedLayers().map(function (layer) {
    var p = layer.feature.properties, point = isPoint(layer);
    p.area_km2 = point ? 0 : Math.round(areaKm2(layer) * 1000) / 1000;
    p.vertices = point ? 1 : vertexCount(layer);
    if (layer.getTooltip()) layer.getTooltip().setLatLng(labelAnchor(layer));
    var f = layer.toGeoJSON();
    f.properties = { id: p.id, name: p.name, kind: point ? 'point' : 'outline',
                     area_km2: p.area_km2, vertices: p.vertices };
    if (p.area_ha !== undefined) f.properties.area_ha = p.area_ha;
    if (p.supplier_id !== undefined) f.properties.supplier_id = p.supplier_id;
    return f;
  });
  if (bridge) bridge.update(JSON.stringify({ type: 'FeatureCollection', features: features }));
  refreshHalos();
  report();
}

function report() {
  if (!bridge) return;
  var pts = drawPoints();
  var live = pts >= 3 ? ringKm2(drawHandler._markers.map(function (m) { return m.getLatLng(); })) : 0;
  bridge.state(JSON.stringify({ mode: mode, points: pts, area_km2: live, selected: selectedId }));
}

// ---------- selection ----------
// fromPanel: the toolbar asked for this, so reveal it on the map
function selectShape(id, fromPanel) {
  selectedId = (id === null || findLayer(id) === undefined) ? null : id;
  drawnItems.eachLayer(function (l) {
    var lid = l.feature.properties.id;
    applyStyle(l, lid === selectedId);
    if (lid === selectedId && !isPoint(l)) l.bringToFront();
  });
  if (selectedId !== null && fromPanel) {
    var b = layerBounds(findLayer(selectedId));
    if (!map.getBounds().contains(b)) map.fitBounds(b, { padding: [80, 80], maxZoom: 17 });
  }
  report();
}
function zoomToShape(id) {
  var layer = findLayer(id);
  if (layer) map.fitBounds(layerBounds(layer), { padding: [80, 80], maxZoom: 17 });
}

map.on('click', function (e) {
  if (mode !== 'idle') return;
  selectShape(null, false);
  send('mapClicked', { lat: e.latlng.lat, lng: e.latlng.lng });
});

// ---------- farms ----------
function labelFor(layer) {
  var p = layer.feature.properties, v = verdicts[p.id];
  if (!v) return esc(p.name);
  if (v.stale) return esc(p.name) + ' · edited';
  var s = CONFIG.verdicts[v.verdict];
  var why = (v.verdict === 'FAIL' || v.verdict === 'PASS') ? '' : ' (' + esc(s.detail) + ')';
  return esc(p.name) + ' · ' + s.icon + ' ' + esc(s.headline) + why;
}

function addFarmLayer(layer, name, extra) {
  var id = nextId++;
  var point = isPoint(layer);
  layer.feature = { type: 'Feature', properties: Object.assign({ id: id, name: name }, extra || {}) };
  layer.bindTooltip(labelFor(layer), {
    permanent: drawnItems.getLayers().length < 150, interactive: false, className: 'shape-label',
    direction: point ? 'top' : 'center', offset: point ? [0, -10] : [0, 0]
  });
  layer.on('click', function (ev) {
    if (mode !== 'idle') return;
    L.DomEvent.stopPropagation(ev);
    selectShape(id, false);
    send('farmClicked', { id: id });
  });
  drawnItems.addLayer(layer);
  applyStyle(layer, false);
  return id;
}

// Farms from a file or from Neon (a FeatureCollection of Point / Polygon / MultiPolygon)
function addFeatures(fc) {
  var added = 0;
  L.geoJSON(fc, { pointToLayer: function (f, latlng) { return L.marker(latlng); } }).eachLayer(function (layer) {
    var p = (layer.feature && layer.feature.properties) || {};
    var name = String(p.name || p.plot_id || p.id || ((isPoint(layer) ? 'Point ' : 'Polygon ') + nextId));
    var extra = {};
    if (p.area_ha !== undefined && p.area_ha !== null) extra.area_ha = p.area_ha;
    if (p.supplier_id) extra.supplier_id = p.supplier_id;
    addFarmLayer(layer, name, extra);
    added++;
  });
  sync();
  if (added) map.fitBounds(drawnItems.getBounds(), { padding: [60, 60], maxZoom: 17 });
  return added;
}

// ---------- verdicts from the last check: Python calls setVerdicts({id: {verdict, stale}}) ----------
function setVerdicts(byId) {
  verdicts = byId || {};
  drawnItems.eachLayer(function (l) {
    applyStyle(l, l.feature.properties.id === selectedId);
    if (l.getTooltip()) l.setTooltipContent(labelFor(l));
  });
  refreshHalos();
}
function refreshHalos() {
  haloGroup.clearLayers();
  drawnItems.eachLayer(function (l) {
    var v = verdicts[l.feature.properties.id];
    if (!v || v.stale || isPoint(l)) return;
    L.polygon(l.getLatLngs(), { pane: 'halos', color: '#ffffff', weight: 7, opacity: 0.9,
                                fill: false, interactive: false }).addTo(haloGroup);
  });
}

// ---------- drawing ----------
function startDraw() {
  if (mode !== 'idle') return;
  selectShape(null, false);
  var c = colorFor(nextId);
  drawHandler.setOptions({ shapeOptions: { color: c, fillColor: c, opacity: 1, weight: 4, fillOpacity: 0.38 } });
  activeDraw = drawHandler;
  drawHandler.enable();
}
function startPoint() {
  if (mode !== 'idle') return;
  selectShape(null, false);
  pointHandler.setOptions({ icon: pointIcon(colorFor(nextId), true) });
  activeDraw = pointHandler;
  pointHandler.enable();
}
function finishDraw() { if (activeDraw === drawHandler && drawPoints() >= 3) drawHandler.completeShape(); }
function undoPoint() { if (activeDraw === drawHandler) drawHandler.deleteLastVertex(); }
function cancelDraw() { if (activeDraw) activeDraw.disable(); }

map.on('draw:drawstart', function (e) {
  mode = e.layerType === 'marker' ? 'placing' : 'drawing';
  map.doubleClickZoom.disable();
  report();
});
map.on('draw:drawstop', function () { mode = 'idle'; activeDraw = null; map.doubleClickZoom.enable(); report(); });
map.on('draw:drawvertex', function () {
  var markers = drawHandler._markers || [];
  markers.forEach(function (m, i) {
    if (m._icon) m._icon.classList.toggle('first-vertex', i === 0 && markers.length >= 3);
  });
  report();
});

map.on(L.Draw.Event.CREATED, function (e) {
  var id = addFarmLayer(e.layer, (e.layerType === 'marker' ? 'Point ' : 'Polygon ') + nextId);
  sync();
  selectShape(id, false);
});

// ---------- editing ----------
function startEdit() {
  if (mode !== 'idle' || drawnItems.getLayers().length === 0) return;
  selectShape(null, false);
  editHandler.enable();
}
function saveEdit() { editHandler.save(); editHandler.disable(); }
function cancelEdit() { editHandler.revertLayers(); editHandler.disable(); }

// Enter / Esc: act on whatever mode the map is in right now
function confirmAction() { if (mode === 'editing') saveEdit(); else finishDraw(); }
function cancelAction() {
  if (mode === 'drawing' || mode === 'placing') cancelDraw();
  else if (mode === 'editing') cancelEdit();
  else { selectShape(null, false); send('escape'); }
}

map.on('draw:editstart', function () { mode = 'editing'; report(); });
map.on('draw:editstop', function () { mode = 'idle'; selectShape(null, false); sync(); });
map.on(L.Draw.Event.EDITED, sync);

// ---------- shape management ----------
function deleteShape(id) {
  var layer = findLayer(id);
  if (!layer) return;
  drawnItems.removeLayer(layer);
  if (selectedId === id) selectedId = null;
  sync();
}
function renameShape(id, name) {
  var layer = findLayer(id);
  if (!layer) return;
  layer.feature.properties.name = name;
  if (layer.getTooltip()) layer.setTooltipContent(labelFor(layer));
  sync();
}
function clearAll() {
  if (mode === 'drawing' || mode === 'placing') cancelDraw();
  if (mode === 'editing') cancelEdit();
  drawnItems.clearLayers();
  selectedId = null;
  sync();
}

new QWebChannel(qt.webChannelTransport, function (channel) {
  bridge = channel.objects.bridge;
  sync();
  bridge.ready();
});
