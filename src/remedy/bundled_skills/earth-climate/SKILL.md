---
name: earth-climate
description: >
  Use for earth, ocean, atmosphere and ecology data work: NetCDF/CF, GRIB, Zarr,
  HDF5, GeoTIFF and vector formats, coordinate reference systems, regridding and
  land/sea masks, non-standard calendars, anomalies and baselines, trends with
  autocorrelation, extremes and return periods, CMIP scenarios and ensemble
  spread, remote-sensing preprocessing, and field-ecology survey data.
version: 1.0.0
author: Remedy
tags: [research, climate, geospatial, remote-sensing, ecology, netcdf]
requires: []
tools: [analysis_env, analysis_run, analysis_ledger, data_profile, data_diff, stats_assumptions, stats_effect_size, lit_search, cite_add, skill_activate, file_read, file_write]
triggers:
  - '\b(NetCDF|\.nc4?\b|\bGRIB2?\b|CMIP[56]|ERA5|MODIS|Landsat|Sentinel-[12])\b'
  - '\b(reanalysis (?:data|product|dataset)|climate (?:model|projection|scenario)|RCP\d(?:\.\d)?|SSP\d-?\d)\b'
  - '\b(detrend\w+|seasonal (?:cycle|decomposition)|anomaly time series|teleconnection|\bENSO\b|\bNAO\b index)\b'
  - '\b(shapefile|geospatial (?:raster|vector)|EPSG:\d+|remote sensing|zonal statistics|reprojection)\b'
---

# Earth, climate and ecology data

Run `skill_activate(skill="research-method")` first and work from that spine —
question framing, evidence standards, citation honesty, what verification means.
Do not restate it. This pack covers what is different when the data is gridded,
projected, dated on a strange calendar and produced by somebody else.

## Before any arithmetic

1. **`analysis_env(path)`.** These stacks live in the project, not in this
   process: xarray, netCDF4, cfgrib, rasterio, GDAL, cdo, nco, geopandas, R
   terra/sf. Everything heavy runs through `analysis_run` in the project's own
   interpreter. Missing tool -> say so and name the install; do not silently
   substitute a worse method.
2. **Record the product identity.** Name, version/DOI, spatial and temporal
   resolution, variable long_name and units, download date, and processing level
   (L1/L2/L3). `cite_add` the dataset by its own DOI — datasets are citable
   objects, and "ERA5" alone is not a citation.
3. **Print the metadata before you trust the array.** `ncdump -h`, `gdalinfo`,
   `xr.open_dataset(...).attrs`. Read units, `_FillValue`/`missing_value`,
   `scale_factor`/`add_offset`, `calendar`, the CRS, and whether latitude runs
   north-to-south. `references/data-formats-and-cf.md`.
4. **Say which kind of data it is and never mix silently.** Observation,
   reanalysis (a model constrained by observations — not an observation), and
   free-running model output are three different epistemic objects. A figure
   that overlays them must label each one.

## Working rules

- **CRS.** Every dataset carries or lacks a CRS; assuming EPSG:4326 because the
  numbers look like degrees is the classic silent error. Reproject deliberately,
  compute areas and distances in an equal-area or local projection, never in
  degrees. `references/crs-and-projections.md`.
- **Area weighting.** A global or regional mean of a lat/lon grid without
  `cos(latitude)` weighting is wrong, and wrong in a direction that flatters
  polar amplification. Use the grid cell areas the tool gives you.
- **Regridding.** Bilinear for smooth fields you will look at; conservative for
  anything that must integrate correctly (precipitation, fluxes, area totals).
  Regrid once, at a documented step, and keep the source grid.
  `references/regridding-and-masks.md`.
- **Masks and calendars.** Land/sea masks differ between products; apply the
  mask that belongs to the grid you are on. Model calendars can be 360-day or
  no-leap: never compare a 360-day series to a Gregorian one by index.
- **Anomalies.** State the baseline period and the climatology construction
  (which years, which smoothing, per-calendar-day or per-month). Two anomaly
  series on different baselines are not comparable.
  `references/trends-and-autocorrelation.md`.
- **Trends.** Geophysical series are autocorrelated, so ordinary least-squares
  standard errors are too small. Report the trend with an interval computed from
  an effective sample size or a method that handles serial correlation, and say
  which. Mann-Kendall with prewhitening or Sen slope where normality fails.
  Check for breakpoints and instrument changes before calling a step a signal.
- **Extremes.** Block maxima (GEV) or peaks-over-threshold (GPD), with the
  threshold and declustering stated. A return period is a probability statement
  about a stationary distribution; if the climate is changing, say so and use a
  non-stationary fit or report the epoch.
  `references/extremes-and-return-periods.md`.
- **Ensembles and scenarios.** A CMIP number is meaningless without its scenario,
  model, variant label and period. Report spread across models, not just the
  mean, and never present a multi-model mean as a prediction.
  `references/cmip-ensembles-and-scenarios.md`.
- **Remote sensing.** Know the processing level, apply cloud/quality masks from
  the product's own QA band, and do not compute indices across bands from
  different atmospheric corrections. `references/remote-sensing.md`.
- **Field ecology.** Detection is not perfect: transects, mark-recapture and
  occupancy models estimate detection probability rather than assuming it.
  `references/ecology-field-data.md`.

Read `references/INDEX.md` and pull what you need with `file_read`.

## What counts as verified here

1. **Units and magnitude check.** Convert to physical units and compare against
   an order-of-magnitude expectation (K vs degC, kg m-2 s-1 vs mm/day, m vs km).
   A sign flip or 86400 factor is the most common real bug in this field.
2. **Round-trip and conservation.** After a conservative regrid, the
   area-weighted global integral matches the source within tolerance — compute
   it and report the residual.
3. **Independent reproduction of a known quantity.** Recompute a value the
   product's own documentation publishes (a global mean, a documented trend) and
   compare. If it does not match, the pipeline is wrong, not the product.
4. **Provenance intact.** Every figure came from an `analysis_run`;
   `analysis_ledger(action="verify", run_id=...)` says INTACT before it goes in
   the paper. `data_diff` between product versions before you blame the science
   for a changed result.
5. **Citations resolve.** `cite_check(manuscript, resolve=True)` returns PASS,
   and the dataset DOIs are in the bibliography alongside the papers.

Say the uncertainty out loud: observational coverage gaps, reanalysis
inhomogeneity across the satellite era, and model structural error are not
covered by the confidence interval you computed.
