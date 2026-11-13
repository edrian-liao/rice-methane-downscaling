// ============================================================
// Sentinel-1 field-mean time series -- the five 2026 fields, ASC + DESC
// Bands: VV, VH, VV/VH ratio (dB and linear), water-like index, incidence angle
//
// Paste into the Earth Engine Code Editor and Run. Check the charts/map, then
// open the Tasks tab and RUN the one export (a single CSV for all fields).
//
// Indices are computed PER PIXEL, then averaged over the field -- the same way
// as the 2021-2024 training CSVs, so the 2026 features are comparable.
// ============================================================

// -----------------------------------------------------------
// 1. USER PARAMETERS
// -----------------------------------------------------------
var START_DATE = '2026-03-15';   // same window as gee_ndvi_fields_2026.js
var END_DATE   = '2026-10-21';   // exclusive

var PASS_DIRS  = ['ASCENDING', 'DESCENDING'];   // both for now; drop one to filter
var ORBIT_NUM  = null;           // e.g. 63 to keep one relative orbit; null = all
var MIN_VALID_FRAC = 0.9;        // flag (not drop) scenes covering < 90% of a field
var DRIVE_FOLDER = 's1_fields_2026';

// -----------------------------------------------------------
// 2. FIELDS -- same polygons as gee_ndvi_fields_2026.js
// -----------------------------------------------------------
// Vertices from final/Fields_2024 - Sheet1.csv. Way 3 and Way 4 each have one
// polygon per gauge (~99% overlap); merged below. S16 traced on geojson.io.
var W3EC = ee.Geometry.Polygon([[[-91.74801008686202, 34.58589120694286], [-91.74790279850143, 34.588752983270226], [-91.75556318744796, 34.58866465831441], [-91.75558464512008, 34.585696885223854]]]);
var W3CS = ee.Geometry.Polygon([[[-91.75558907169768, 34.58571216346502], [-91.74797159809539, 34.58585348837066], [-91.74795014042327, 34.58871526599704], [-91.75567490238616, 34.58871526599704]]]);
var W4EC = ee.Geometry.Polygon([[[-91.75570267660576, 34.58197559241577], [-91.747999372315, 34.581904926695636], [-91.74802082998711, 34.58513782188647], [-91.75563830358941, 34.58503182731693]]]);
var W4CS = ee.Geometry.Polygon([[[-91.75578368888281, 34.58205944539639], [-91.74805892691992, 34.58195344690055], [-91.74808038459204, 34.58515100871642], [-91.75571931586646, 34.58509801145696]]]);
var R8   = ee.Geometry.Polygon([[[-91.86403982803346, 34.84190524862535], [-91.86603539154054, 34.838999384130325], [-91.85843937561036, 34.83892893765686], [-91.85839646026612, 34.84194047059605]]]);
var S0   = ee.Geometry.Polygon([[[-91.76682873518074, 34.49424016333888], [-91.76693602354133, 34.491198279042706], [-91.76333113462532, 34.49112753622719], [-91.76307364255989, 34.494169423103884]]]);
var S16  = ee.Geometry.Polygon([[[-91.8104851, 34.8514213], [-91.8103486, 34.8499279], [-91.8097117, 34.8494612], [-91.8050485, 34.8493679], [-91.8048893, 34.8545573], [-91.8076644, 34.8524667]]]);

var fields = ee.FeatureCollection([
  ee.Feature(W3EC.union(W3CS, 1), {name: 'Way 3'}),
  ee.Feature(W4EC.union(W4CS, 1), {name: 'Way 4'}),
  ee.Feature(R8,  {name: '4 East'}),
  ee.Feature(S0,  {name: 'South 0'}),
  ee.Feature(S16, {name: 'S16'})
]);
var NAMES = ['Way 3', 'Way 4', '4 East', 'South 0', 'S16'];

// Rectangle around all fields, to find scenes. Not fields.geometry(): merging
// the shapes fails with "specify a non-zero error margin".
var AOI = ee.Geometry.Rectangle([-91.876, 34.481, -91.738, 34.864]);

Map.centerObject(AOI, 10);
Map.setOptions('SATELLITE');
Map.addLayer(ee.Image().byte().paint(fields, 1, 2), {palette: ['orange']}, 'Field boundaries');

// -----------------------------------------------------------
// 3. LOAD SENTINEL-1 GRD COLLECTION (values are already in dB)
// -----------------------------------------------------------
var s1 = ee.ImageCollection('COPERNICUS/S1_GRD')
  .filterBounds(AOI)
  .filterDate(START_DATE, END_DATE)
  .filter(ee.Filter.eq('instrumentMode', 'IW'))
  .filter(ee.Filter.inList('orbitProperties_pass', PASS_DIRS))
  .filter(ee.Filter.listContains('transmitterReceiverPolarisation', 'VV'))
  .filter(ee.Filter.listContains('transmitterReceiverPolarisation', 'VH'))
  .select(['VV', 'VH', 'angle']);

if (ORBIT_NUM !== null) {
  s1 = s1.filter(ee.Filter.eq('relativeOrbitNumber_start', ORBIT_NUM));
}

print('Number of S1 images found:', s1.size());
// Which orbits/directions you are getting -- each one is its own viewing geometry.
print('Images per pass direction:', s1.aggregate_histogram('orbitProperties_pass'));
print('Images per relative orbit:', s1.aggregate_histogram('relativeOrbitNumber_start'));

// -----------------------------------------------------------
// 4. INDEX COMPUTATION (unchanged from your script)
// -----------------------------------------------------------
function toLinear(image) {
  return ee.Image(10).pow(image.divide(10)).rename(image.bandNames());
}

function addIndices(image) {
  var vvLin = toLinear(image.select('VV'));
  var vhLin = toLinear(image.select('VH'));
  var ratioLin = vvLin.divide(vhLin).rename('VV_VH_ratio_lin');
  var ratioDB = image.select('VV').subtract(image.select('VH')).rename('VV_VH_ratio_dB');
  var wli = vhLin.subtract(vvLin).divide(vhLin.add(vvLin)).rename('WaterLike_Index');
  return image.addBands(ratioDB).addBands(ratioLin).addBands(wli);
}

var processed = s1.map(addIndices);

// -----------------------------------------------------------
// 5. FIELD MEANS: one row per (image, field)
// -----------------------------------------------------------
var BANDS = ['VV', 'VH', 'VV_VH_ratio_dB', 'VV_VH_ratio_lin', 'WaterLike_Index', 'angle'];

// Per field: the mean of each band, plus two pixel counts on the image's own
// 10 m grid -- pixels with data, and pixels the field would have if fully
// covered -- so you can spot scenes that only clip a field (valid_frac < 1).
// The two reducers take separate inputs (sharedInputs = false): the first
// gets the 6 BANDS, the second gets 'cnt' and 'full'.
var REDUCER = ee.Reducer.mean().forEach(BANDS)
  .combine(ee.Reducer.count().forEach(['n_pixels', 'n_full']), null, false);

function extractFieldMeans(image) {
  var pass = ee.String(image.get('orbitProperties_pass'));
  // Approximate radar look azimuth (as in your script).
  var azimuth = ee.Number(ee.Algorithms.If(pass.equals('ASCENDING'), 80, 260));

  var input = image.select(BANDS)
    .addBands(image.select('VV').rename('n_pixels'))           // valid pixels
    .addBands(image.select('VV').unmask(0).rename('n_full'));  // all pixels

  var reduced = input.reduceRegions({collection: fields, reducer: REDUCER, scale: 10});

  return reduced.map(function (f) {
    var n = ee.Number(f.get('n_pixels'));
    return ee.Feature(null, {
      'field':             f.get('name'),
      'date':              image.date().format('YYYY-MM-dd'),
      'time_utc':          image.date().format('YYYY-MM-dd HH:mm:ss'),
      'pass':              pass,
      'relative_orbit':    image.get('relativeOrbitNumber_start'),
      'platform':          image.get('platform_number'),
      'VV_dB':             f.get('VV'),
      'VH_dB':             f.get('VH'),
      'VV_VH_ratio_dB':    f.get('VV_VH_ratio_dB'),
      'VV_VH_ratio_lin':   f.get('VV_VH_ratio_lin'),
      'WaterLike_Index':   f.get('WaterLike_Index'),
      'incidence_angle':   f.get('angle'),
      'zenith_angle':      f.get('angle'),
      'azimuth_angle':     azimuth,
      'n_pixels':          n,
      'valid_frac':        n.divide(ee.Number(f.get('n_full')).max(1)),
      'system:time_start': image.get('system:time_start')
    });
  // Drop fields this scene doesn't touch at all (VV mean is null there).
  }).filter(ee.Filter.notNull(['VV_dB']));
}

var tsCollection = ee.FeatureCollection(processed.map(extractFieldMeans)).flatten();
print('Rows (image x field):', tsCollection.size());
print('First rows:', tsCollection.limit(10));
print('Rows covering < ' + MIN_VALID_FRAC * 100 + '% of their field (kept, check valid_frac):',
      tsCollection.filter(ee.Filter.lt('valid_frac', MIN_VALID_FRAC)).size());

// -----------------------------------------------------------
// 6. CHARTS: one line per field, ascending and descending separately
// -----------------------------------------------------------
function chart(passDir, band, title) {
  return ui.Chart.feature.groups(
      tsCollection.filter(ee.Filter.eq('pass', passDir)),
      'system:time_start', band, 'field')
    .setChartType('LineChart')
    .setOptions({
      title: title + ' -- ' + passDir,
      hAxis: {title: 'Date', format: 'MMM'},
      vAxis: {title: band},
      lineWidth: 1, pointSize: 4, interpolateNulls: true
    });
}
PASS_DIRS.forEach(function (p) {
  print(chart(p, 'VV_dB', 'VV backscatter (dB)'));
  print(chart(p, 'VH_dB', 'VH backscatter (dB)'));
  print(chart(p, 'VV_VH_ratio_dB', 'VV/VH ratio (dB)'));
  print(chart(p, 'WaterLike_Index', 'Water-like index'));
});

// -----------------------------------------------------------
// 7. EXPORT: one CSV, all fields, both directions
// -----------------------------------------------------------
Export.table.toDrive({
  collection: tsCollection,
  description: 'S1_fields_2026',
  folder: DRIVE_FOLDER,
  fileNamePrefix: 'S1_fields_2026',
  fileFormat: 'CSV',
  selectors: [
    'field', 'date', 'time_utc', 'pass', 'relative_orbit', 'platform',
    'VV_dB', 'VH_dB', 'VV_VH_ratio_dB', 'VV_VH_ratio_lin', 'WaterLike_Index',
    'incidence_angle', 'zenith_angle', 'azimuth_angle', 'n_pixels', 'valid_frac'
  ]
});

// -----------------------------------------------------------
// 8. MAP: latest image of each direction, clipped to the fields
// -----------------------------------------------------------
PASS_DIRS.forEach(function (p) {
  var latest = processed.filter(ee.Filter.eq('orbitProperties_pass', p))
    .sort('system:time_start', false).first();
  Map.addLayer(latest.select('VV').clipToCollection(fields),
    {min: -25, max: 0, palette: ['black', 'white']}, 'Latest VV (dB) ' + p, false);
  Map.addLayer(latest.select('VH').clipToCollection(fields),
    {min: -30, max: -5, palette: ['black', 'white']}, 'Latest VH (dB) ' + p, false);
  Map.addLayer(latest.select('WaterLike_Index').clipToCollection(fields),
    {min: -0.5, max: 0.5, palette: ['#e74c3c', '#f5f5f5', '#2980b9']},
    'Latest water-like index ' + p, false);
});
