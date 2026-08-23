# Remote sensing basics

## Know the processing level

- **L1** — calibrated radiance or top-of-atmosphere reflectance, geolocated.
- **L2** — geophysical variables retrieved per pixel (surface reflectance, LST,
  chlorophyll), with a quality/QA layer.
- **L3** — gridded and composited in space and time.
Never compute an index across bands from different levels or different
atmospheric corrections. Surface reflectance and top-of-atmosphere reflectance
are different quantities; mixing them produces indices that look plausible and
are wrong.

Record the collection/version (for example a Landsat Collection number or a
MODIS collection); reprocessing campaigns change values, and `data_diff` between
versions is the way to find out whether a result moved because of the science or
because of the archive.

## Bands and indices

Band numbering differs between sensors — the same index needs different band
combinations for Landsat 8/9, Sentinel-2 and MODIS. Look the mapping up in the
mission documentation each time. Common normalised-difference indices (NDVI,
NDWI, NBR and relatives) are ratios of two bands; they saturate at high biomass,
are sensitive to soil background at low cover, and are not interchangeable
across sensors without cross-calibration. If comparing sensors, apply and cite a
published harmonisation, or restrict the analysis to one sensor.

## Masking

Apply the product's own QA/cloud/shadow/snow mask before any statistic; cloud
edges and shadows bias indices strongly and systematically. Cloud masks miss
thin cirrus and over-flag bright surfaces — inspect a few scenes. State the
mask, the bits used and the resulting valid-pixel fraction per scene or
composite; a composite built from few clear observations is not comparable with
one built from many, and clear-sky sampling is itself biased toward dry days.

## Geometry and time

- Resolution is not the same as the point spread function; a 10 m pixel does not
  resolve a 10 m object.
- Mixed pixels dominate at coarse resolution; a "forest" pixel at 500 m is a
  fraction, and unmixing is a model with its own error.
- Off-nadir viewing and BRDF effects change apparent reflectance with view and
  sun angle; use BRDF-corrected products for time series.
- Revisit time, orbit drift and local overpass time shape a "trend"; check
  whether the overpass time changed during the record.
- Geolocation error between sensors means per-pixel comparison needs
  coregistration; check with a shared feature.

## Validation

A satellite product is a retrieval, not a measurement. Validate against ground
or airborne reference where any exists, report the number of matchups, the
matchup criteria (distance and time window), bias and RMSE, and the range over
which validation holds. Where no reference exists, say the product is
unvalidated for this application rather than reporting it as truth.
