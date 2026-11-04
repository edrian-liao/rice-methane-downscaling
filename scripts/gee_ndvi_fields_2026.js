/*
 * Sentinel-2 NDVI for the five 2026 fields -> one GeoTIFF per field.
 *
 * Paste into the Earth Engine Code Editor (code.earthengine.google.com) and Run.
 *
 * 1. PREVIEW (default): pick a date and a field in the panel on the left. You
 *    see true colour, masked NDVI, the field outlines and the gauges. The
 *    console shows a field-mean NDVI chart for every date.
 * 2. EXPORT: when the map looks right, set EXPORT = true and Run again. Tasks
 *    then appear in the Tasks tab (top right); click RUN on each one.
 *
 * Each GeoTIFF = one field, one band per date, named NDVI_YYYYMMDD.
 * Pixels that are cloud/shadow, or outside the field polygon, = NODATA.
 */

// ============================ CONFIG ========================================
var START = '2026-03-15';
var END = '2026-10-21';            // exclusive
var MAX_SCENE_CLOUD = 80;          // skip whole scenes cloudier than this (%); 100 = keep all
// SCL classes kept: 4 vegetation, 5 bare soil, 6 WATER. Keep 6: a flooded
// paddy before canopy closure is classed as water, and dropping it would
// remove exactly the flooded fields.
var KEEP_SCL = [4, 5, 6];
var SCALE = 10;                    // metres
var CRS = 'EPSG:32615';            // UTM 15N, same as the NISAR clips
var BUFFER_M = 50;                 // export window = polygon + this margin
var CLIP_TO_FIELD = true;          // true: pixels outside the polygon -> NODATA
var NODATA = -9999;
var DRIVE_FOLDER = 'ndvi_fields_2026';
var EXPORT = false;                // set true AFTER checking the map

// ============================ FIELDS ========================================
// Vertices from final/Fields_2024 - Sheet1.csv. Way 3 and Way 4 each have one
// polygon per gauge (~99% overlap); they are merged below.
var W3EC = ee.Geometry.Polygon([[[-91.74801008686202, 34.58589120694286], [-91.74790279850143, 34.588752983270226], [-91.75556318744796, 34.58866465831441], [-91.75558464512008, 34.585696885223854]]]);
var W3CS = ee.Geometry.Polygon([[[-91.75558907169768, 34.58571216346502], [-91.74797159809539, 34.58585348837066], [-91.74795014042327, 34.58871526599704], [-91.75567490238616, 34.58871526599704]]]);
var W4EC = ee.Geometry.Polygon([[[-91.75570267660576, 34.58197559241577], [-91.747999372315, 34.581904926695636], [-91.74802082998711, 34.58513782188647], [-91.75563830358941, 34.58503182731693]]]);
var W4CS = ee.Geometry.Polygon([[[-91.75578368888281, 34.58205944539639], [-91.74805892691992, 34.58195344690055], [-91.74808038459204, 34.58515100871642], [-91.75571931586646, 34.58509801145696]]]);
var R8 = ee.Geometry.Polygon([[[-91.86403982803346, 34.84190524862535], [-91.86603539154054, 34.838999384130325], [-91.85843937561036, 34.83892893765686], [-91.85839646026612, 34.84194047059605]]]);
var S0 = ee.Geometry.Polygon([[[-91.76682873518074, 34.49424016333888], [-91.76693602354133, 34.491198279042706], [-91.76333113462532, 34.49112753622719], [-91.76307364255989, 34.494169423103884]]]);
// S16 is in no Fields_*.csv. Field outline traced on geojson.io over NISAR backscatter.
var S16 = ee.Geometry.Polygon([[[-91.8104851, 34.8514213], [-91.8103486, 34.8499279], [-91.8097117, 34.8494612], [-91.8050485, 34.8493679], [-91.8048893, 34.8545573], [-91.8076644, 34.8524667]]]);

var fields = ee.FeatureCollection([
  ee.Feature(W3EC.union(W3CS, 1), {name: 'Way 3'}),
  ee.Feature(W4EC.union(W4CS, 1), {name: 'Way 4'}),
  ee.Feature(R8, {name: '4 East'}),
  ee.Feature(S0, {name: 'South 0'}),
  ee.Feature(S16, {name: 'S16'})
]);
// Client-side copy of the names, for the dropdown and the export loop.
var NAMES = ['Way 3', 'Way 4', '4 East', 'South 0', 'S16'];

// Rectangle around all five fields, used to find scenes and to centre the map.
// Deliberately NOT fields.geometry(): merging the field shapes into one makes
// Earth Engine fail with "specify a non-zero error margin". Widen it if you
// add a field elsewhere.
var AOI = ee.Geometry.Rectangle([-91.876, 34.481, -91.738, 34.864]);

// Gauges, for reference on the map (from scripts/gauges_2026.csv).
var gauges = ee.FeatureCollection([
  ee.Feature(ee.Geometry.Point([-91.75176, 34.58869]), {id: 'W3-EC'}),
  ee.Feature(ee.Geometry.Point([-91.75182, 34.58557]), {id: 'W3-CS'}),
  ee.Feature(ee.Geometry.Point([-91.75159, 34.58494]), {id: 'W4-EC'}),
  ee.Feature(ee.Geometry.Point([-91.75164, 34.58195]), {id: 'W4-CS'}),
  ee.Feature(ee.Geometry.Point([-91.81026, 34.85023]), {id: 'S16'}),
  ee.Feature(ee.Geometry.Point([-91.86522, 34.84044]), {id: 'R8'}),
  ee.Feature(ee.Geometry.Point([-91.76692, 34.49382]), {id: 'S0'})
]);

// ============================ NDVI ==========================================
// Harmonized L2A: the +1000 reflectance offset (baseline >= 04.00) is already
// removed, so NDVI is computed directly.
var s2 = ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED')
    .filterBounds(AOI)
    .filterDate(START, END)
    .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', MAX_SCENE_CLOUD));

function maskedNdvi(img) {
  // 1 where the SCL class is in KEEP_SCL, 0 elsewhere -> masked.
  var ok = img.select('SCL').remap(KEEP_SCL, ee.List.repeat(1, KEEP_SCL.length), 0);
  return img.normalizedDifference(['B8', 'B4']).rename('NDVI').updateMask(ok);
}

// The fields straddle two MGRS tiles, so a date can have two scenes. Mosaic
// them into one image per day.
var days = ee.List(s2.aggregate_array('system:time_start'))
    .map(function (t) { return ee.Date(t).format('YYYY-MM-dd'); })
    .distinct().sort();

var dailyNdvi = ee.ImageCollection(days.map(function (d) {
  var day = ee.Date(d);
  return s2.filterDate(day, day.advance(1, 'day')).map(maskedNdvi).mosaic()
      .set('system:time_start', day.millis(), 'date', d);
}));

// One band per date: NDVI_YYYYMMDD.
var bandNames = days.map(function (d) {
  return ee.String('NDVI_').cat(ee.Date(d).format('YYYYMMdd'));
});
var stack = dailyNdvi.toBands().rename(bandNames);

print('Dates with a scene (after the cloud filter):', days.length(), days);

// ============================ PREVIEW =======================================
var NDVI_VIS = {min: 0, max: 0.9, palette: ['8c510a', 'd8b365', 'f6e8c3', 'c7eae5', '5ab4ac', '01665e']};
var outlines = ee.Image().byte().paint(fields, 1, 2);    // 2-px outlines

function show(d) {
  var day = ee.Date(d);
  var scenes = s2.filterDate(day, day.advance(1, 'day'));
  Map.layers().reset([
    ui.Map.Layer(scenes.mosaic().select(['B4', 'B3', 'B2']),
                 {min: 0, max: 3000}, 'true colour ' + d),
    ui.Map.Layer(scenes.map(maskedNdvi).mosaic(), NDVI_VIS,
                 'NDVI ' + d + ' (gaps = cloud/shadow)'),
    ui.Map.Layer(outlines, {palette: ['00e5ff']}, 'field polygons'),
    ui.Map.Layer(gauges, {color: 'ff0000'}, 'gauges')
  ]);
}

var dateSelect = ui.Select({placeholder: 'loading dates...', onChange: show});
var fieldSelect = ui.Select({
  items: NAMES, placeholder: 'zoom to field',
  onChange: function (name) {
    Map.centerObject(ee.Feature(fields.filter(ee.Filter.eq('name', name)).first()).geometry(), 15);
  }
});
ui.root.insert(0, ui.Panel({
  widgets: [ui.Label('Date'), dateSelect, ui.Label('Field'), fieldSelect,
            ui.Label('EXPORT = ' + EXPORT, {color: EXPORT ? 'red' : 'grey'})],
  style: {width: '230px'}
}));

days.evaluate(function (list, err) {     // fill the dropdown, show a mid-season date
  if (err || !list) { print('Could not list dates:', err); return; }
  if (list.length === 0) { print('No scenes in the date range -- check START/END.'); return; }
  dateSelect.items().reset(list);
  dateSelect.setValue(list[Math.floor(list.length / 2)]);
});
Map.centerObject(AOI, 10);
Map.setOptions('SATELLITE');

// Field-mean NDVI per date. Gaps = that field was fully masked on that date.
print(ui.Chart.image.seriesByRegion(dailyNdvi, fields, ee.Reducer.mean(), 'NDVI',
                                    SCALE, 'system:time_start', 'name')
    .setOptions({title: 'Field-mean NDVI, ' + START + ' to ' + END,
                 vAxis: {title: 'NDVI', viewWindow: {min: 0, max: 1}},
                 lineWidth: 1, pointSize: 4}));

// ============================ EXPORT ========================================
if (EXPORT) {
  NAMES.forEach(function (name) {
    var geom = ee.Feature(fields.filter(ee.Filter.eq('name', name)).first()).geometry();
    var img = CLIP_TO_FIELD ? stack.clip(geom) : stack;
    var safe = name.replace(/[^A-Za-z0-9]+/g, '_').replace(/_$/, '');
    Export.image.toDrive({
      image: img.unmask(NODATA).toFloat(),     // masked -> NODATA, not 0
      description: 'NDVI_2026_' + safe,
      folder: DRIVE_FOLDER,
      fileNamePrefix: 'NDVI_2026_' + safe,
      region: geom.buffer(BUFFER_M, 1).bounds(1),
      scale: SCALE,
      crs: CRS,
      maxPixels: 1e9,
      fileFormat: 'GeoTIFF'
    });
  });
  // The polygons themselves, and the date of every band, alongside the rasters.
  Export.table.toDrive({collection: fields, description: 'fields_2026_polygons',
                        folder: DRIVE_FOLDER, fileFormat: 'GeoJSON'});
  Export.table.toDrive({
    collection: ee.FeatureCollection(days.map(function (d) {
      return ee.Feature(null, {date: d, band: ee.String('NDVI_').cat(ee.Date(d).format('YYYYMMdd'))});
    })),
    description: 'ndvi_2026_band_dates', folder: DRIVE_FOLDER, fileFormat: 'CSV'
  });
  print('Export tasks created -- open the Tasks tab and click RUN on each.');
}
