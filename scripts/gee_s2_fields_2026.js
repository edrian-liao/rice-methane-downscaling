// ============================================================
// Sentinel-2 full band stack + NDVI & NDWI -- the five 2026 fields
// Cloud masking via Cloud Score+ (cs_cdf threshold)
// Same-day tiles mosaicked into one image per date
// 10 bands + NDVI + NDWI + angles, spatially averaged per field
//
// Paste into the Earth Engine Code Editor and Run. Check the charts/map, then
// open the Tasks tab and RUN the one export (a single CSV for all fields).
//
// Processing is unchanged from the 2021-2024 script (Cloud Score+ 0.60, daily
// mosaic, 20 m reduction, indices computed per pixel then averaged), so the
// 2026 features are comparable with the training CSVs.
// ============================================================

// -----------------------------------------------------------
// 1. USER PARAMETERS
// -----------------------------------------------------------
var START_DATE    = '2026-03-15';   // same window as the S1 and NDVI exports
var END_DATE      = '2026-10-21';   // exclusive
var CS_CDF_THRESH = 0.60;           // keep pixels with Cloud Score+ cs_cdf >= this
var DRIVE_FOLDER  = 's2_fields_2026';

var BANDS = ['B2','B3','B4','B5','B6','B7','B8','B8A','B11','B12','NDVI','NDWI'];

// -----------------------------------------------------------
// 2. FIELDS -- same polygons as the S1 and NDVI scripts
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

// Rectangle around all fields, to find scenes. Not fields.geometry(): merging
// the shapes fails with "specify a non-zero error margin".
var AOI = ee.Geometry.Rectangle([-91.876, 34.481, -91.738, 34.864]);

Map.centerObject(AOI, 10);
Map.setOptions('SATELLITE');
Map.addLayer(ee.Image().byte().paint(fields, 1, 2), {palette: ['00ff00']}, 'Field boundaries');
Map.addLayer(gauges, {color: 'red'}, 'Gauges');

// -----------------------------------------------------------
// 3. LOAD SENTINEL-2 SR AND CLOUD SCORE+ COLLECTIONS
// -----------------------------------------------------------
var s2 = ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED')
  .filterBounds(AOI)
  .filterDate(START_DATE, END_DATE)
  .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', 80));

var csPlus = ee.ImageCollection('GOOGLE/CLOUD_SCORE_PLUS/V1/S2_HARMONIZED')
  .filterBounds(AOI)
  .filterDate(START_DATE, END_DATE);

// -----------------------------------------------------------
// 4. JOIN CLOUD SCORE+ ONTO S2, MASK CLOUDS, ADD INDICES (unchanged)
// -----------------------------------------------------------
var linked = s2.linkCollection(csPlus, ['cs', 'cs_cdf']);

function maskClouds(image) {
  return image.updateMask(image.select('cs_cdf').gte(CS_CDF_THRESH));
}

function addIndices(image) {
  var ndvi = image.normalizedDifference(['B8', 'B4']).rename('NDVI');
  var ndwi = image.normalizedDifference(['B3', 'B8']).rename('NDWI');
  return image.addBands(ndvi).addBands(ndwi);
}

var processed = linked.map(maskClouds).map(addIndices);

// -----------------------------------------------------------
// 5. ONE MOSAIC PER DATE + AVERAGED ANGLES (unchanged)
// -----------------------------------------------------------
var distinctDates = processed
  .map(function (img) { return ee.Feature(null, {'date': img.date().format('YYYY-MM-dd')}); })
  .distinct('date')
  .aggregate_array('date');

print('Unique acquisition dates:', distinctDates);

var mosaicByDate = ee.ImageCollection(
  distinctDates.map(function (dateStr) {
    dateStr = ee.String(dateStr);
    var start = ee.Date(dateStr);
    var dayColl = processed.filterDate(start, start.advance(1, 'day'));
    return dayColl.mosaic()
      .set('system:time_start', start.millis())
      .set('date',            dateStr)
      .set('solar_zenith',    dayColl.aggregate_mean('MEAN_SOLAR_ZENITH_ANGLE'))
      .set('solar_azimuth',   dayColl.aggregate_mean('MEAN_SOLAR_AZIMUTH_ANGLE'))
      .set('view_zenith_B4',  dayColl.aggregate_mean('MEAN_INCIDENCE_ZENITH_ANGLE_B4'))
      .set('view_azimuth_B4', dayColl.aggregate_mean('MEAN_INCIDENCE_AZIMUTH_ANGLE_B4'))
      .set('view_zenith_B8',  dayColl.aggregate_mean('MEAN_INCIDENCE_ZENITH_ANGLE_B8'))
      .set('view_azimuth_B8', dayColl.aggregate_mean('MEAN_INCIDENCE_AZIMUTH_ANGLE_B8'));
  })
);

print('Total images after deduplication:', mosaicByDate.size());

// -----------------------------------------------------------
// 6. FIELD MEANS: one row per (date, field)
// -----------------------------------------------------------
// Per field: the mean of each band, plus two pixel counts -- cloud-free pixels
// and all pixels -- so clear_frac says how much of the field was usable.
// Rows are KEPT when a field is fully cloudy (bands empty, angles present),
// as in the 2021-2024 CSVs; filter on clear_frac downstream.
// sharedInputs = false: the first reducer gets the 12 BANDS, the second gets
// 'n_clear' and 'n_full'.
var REDUCER = ee.Reducer.mean().forEach(BANDS)
  .combine(ee.Reducer.count().forEach(['n_clear', 'n_full']), null, false);

var ANGLES = ['solar_zenith', 'solar_azimuth', 'view_zenith_B4', 'view_azimuth_B4',
              'view_zenith_B8', 'view_azimuth_B8'];

function extractFieldMeans(image) {
  var input = image.select(BANDS)
    .addBands(image.select('B4').rename('n_clear'))            // cloud-free pixels
    .addBands(image.select('B4').unmask(0).rename('n_full'));  // all pixels

  var reduced = input.reduceRegions({collection: fields, reducer: REDUCER, scale: 20});

  return reduced.map(function (f) {
    var props = {
      'field': f.get('name'),
      'date':  image.get('date'),
      'n_clear': f.get('n_clear'),
      'clear_frac': ee.Number(f.get('n_clear')).divide(ee.Number(f.get('n_full')).max(1)),
      'system:time_start': image.get('system:time_start')
    };
    var feat = ee.Feature(null, props);
    BANDS.forEach(function (b) { feat = feat.set(b, f.get(b)); });
    ANGLES.forEach(function (a) { feat = feat.set(a, image.get(a)); });
    return feat;
  });
}

var tsCollection = ee.FeatureCollection(mosaicByDate.map(extractFieldMeans)).flatten();
print('Rows (date x field):', tsCollection.size());
print('Rows with >= 50% of the field cloud-free:',
      tsCollection.filter(ee.Filter.gte('clear_frac', 0.5)).size());
print('First rows:', tsCollection.limit(10));

// -----------------------------------------------------------
// 7. CHARTS: one line per field
// -----------------------------------------------------------
function chart(band, title, vmin, vmax) {
  return ui.Chart.feature.groups(
      tsCollection.filter(ee.Filter.gte('clear_frac', 0.5)),   // plot only mostly-clear rows
      'system:time_start', band, 'field')
    .setChartType('LineChart')
    .setOptions({
      title: title + ' (rows >= 50% cloud-free)',
      hAxis: {title: 'Date', format: 'MMM'},
      vAxis: {title: band, viewWindow: {min: vmin, max: vmax}},
      lineWidth: 1, pointSize: 4, interpolateNulls: true
    });
}
print(chart('NDVI', 'NDVI', 0, 1));
print(chart('NDWI', 'NDWI (B3-B8)/(B3+B8)', -1, 0.5));
print(chart('B11', 'B11 SWIR1 reflectance (scaled)', 0, 4000));

// -----------------------------------------------------------
// 8. EXPORT: one CSV, all fields
// -----------------------------------------------------------
var COLUMNS = ['field', 'date'].concat(BANDS).concat(ANGLES).concat(['n_clear', 'clear_frac']);

Export.table.toDrive({
  collection:     tsCollection,
  description:    'S2_fields_2026',
  folder:         DRIVE_FOLDER,
  fileNamePrefix: 'S2_fields_2026',
  fileFormat:     'CSV',
  selectors:      COLUMNS
});

// -----------------------------------------------------------
// 9. MAP: latest date, clipped to the fields (may be partly cloudy)
// -----------------------------------------------------------
var latestImage = mosaicByDate.sort('system:time_start', false).first();
Map.addLayer(latestImage.select('NDVI').clipToCollection(fields),
  {min: 0, max: 1, palette: ['#d73027', '#fee08b', '#1a9850']},
  'Latest NDVI', false);
Map.addLayer(latestImage.select('B11').clipToCollection(fields),
  {min: 0, max: 3000, palette: ['#f7fcf5', '#74c476', '#00441b']},
  'Latest B11 SWIR1', false);
