# Formats, CF metadata and calendars

## The formats you will meet

- **NetCDF-4 / HDF5** — self-describing arrays with attributes. Read with
  xarray (`open_dataset`, `open_mfdataset`) or netCDF4; inspect with
  `ncdump -h`.
- **GRIB1/GRIB2** — the weather-service message format. Read with cfgrib/eccodes
  or `wgrib2`; a GRIB file mixes levels and steps, so filter by `typeOfLevel`
  and `shortName` rather than assuming one variable.
- **Zarr** — chunked array store for object storage; the cloud form of the same
  data model. Check the chunking matches your access pattern before a big read.
- **GeoTIFF / COG** — raster with georeferencing; rasterio or GDAL.
- **Shapefile / GeoPackage / GeoJSON** — vector. Shapefile silently truncates
  field names to 10 characters and has no CRS beyond a sidecar `.prj`; prefer
  GeoPackage for new work.
- **CSV of station data** — still common; `data_profile` it for duplicated
  station-time keys, sentinel values and mixed units.

## CF conventions

The Climate and Forecast metadata conventions define `standard_name`, `units`,
`axis`, `cell_methods`, `bounds` and coordinate semantics. Confirm the current
version and vocabulary at the CF conventions site rather than from memory.
Check on open:

- `units` on every variable and on time (`days since 1850-01-01`).
- `standard_name` — the machine-readable identity; `long_name` is prose.
- `cell_methods` — is this an instantaneous value, a mean, a maximum, and over
  what interval? A daily mean and a daily max are different variables.
- `_FillValue` / `missing_value`, and `scale_factor` / `add_offset` — packed
  integers become nonsense if applied twice or not at all.
- Coordinate bounds (`lat_bnds`, `time_bnds`) — required for conservative
  regridding and for knowing whether a timestamp labels the start, middle or end
  of the interval.
- `grid_mapping` — the CRS for anything not plain lat/lon (rotated pole,
  Lambert conformal, polar stereographic).

## Time and calendars

Model output can use `noleap`/`365_day`, `360_day`, `all_leap`, `julian` or
`proleptic_gregorian`. Use cftime-aware handling; do not convert to a pandas
datetime index without deciding what happens to 30 February. Never align two
series by integer index across different calendars — align on real dates and
say how mismatched days were handled.

Also check: is the time stamp UTC or local, does the daily aggregation use a
local day, and does the record label the beginning or end of an accumulation
period? Precipitation accumulation labelling is a classic off-by-one-day bug.

## Practical hygiene

- Run the read and the summary through `analysis_run` so the file hashes are in
  the ledger; `data_diff` between two product versions before blaming science.
- Chunk along the dimension you reduce over; a global time-mean on
  time-chunked data is fast, on space-chunked data it is not.
- Record the exact file list and checksums — "ERA5 monthly means" is not a
  reproducible input.
