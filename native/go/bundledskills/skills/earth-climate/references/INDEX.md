# earth-climate references

- data-formats-and-cf.md — NetCDF/HDF5, GRIB, Zarr, GeoTIFF and vector formats, CF attributes to check on open, packed values, non-standard calendars and timestamp conventions; read before the first read of an unfamiliar file.
- crs-and-projections.md — reading the CRS, axis order and longitude conventions, area and distance in the right projection, categorical resampling, zonal statistics; read before any map, area total or spatial join.
- regridding-and-masks.md — nearest/bilinear/conservative remapping and when each is required, cos-latitude and cell-area weighting, land/sea and quality masks, the conservation check; read before combining two grids.
- trends-and-autocorrelation.md — baselines and climatologies, anomaly construction, autocorrelation and effective sample size, Mann-Kendall and Sen slope, breakpoints, field significance; read before reporting any trend.
- extremes-and-return-periods.md — block maxima vs peaks-over-threshold, declustering, what a return period does and does not claim, non-stationarity, attribution framing; read before any extreme-event statistic.
- cmip-ensembles-and-scenarios.md — CMIP labels and variant ids, RCP/SSP scenarios, internal vs model vs scenario uncertainty, model non-independence, bias correction, dataset citation; read before quoting model output.
- remote-sensing.md — processing levels, band mappings and indices across sensors, cloud and QA masking, viewing geometry and overpass drift, matchup validation; read before computing an index or a satellite time series.
- ecology-field-data.md — detection probability, occupancy and distance sampling and mark-recapture, effort recording, overdispersed counts, taxonomy and vouchers, permits and sensitive-location handling; read for field survey data.
