# Coordinate reference systems

## Read the CRS, never assume it

Degrees-looking numbers are not proof of EPSG:4326. A dataset can be in a
different geographic datum, a rotated pole, or a projected system whose units
happen to look plausible. `gdalinfo`, `rasterio`'s `.crs`, `geopandas`'s `.crs`
and the CF `grid_mapping` attribute are the authorities. If a layer has no CRS,
find the documentation; do not guess and carry on.

## The mistakes that move everything silently

- **Assuming WGS84 for a national grid.** A missing datum shift can offset data
  by tens to hundreds of metres — small enough to look fine on a map, large
  enough to change a zonal statistic.
- **Lat/lon order.** EPSG axis order for geographic CRSs is latitude then
  longitude; most software uses x,y = lon,lat. Mixed conventions produce points
  in the ocean at the mirrored coordinate. Check one known landmark.
- **Longitude convention.** 0-360 vs -180-180. Rolling the axis without sorting
  produces a discontinuity that shows up as a stripe at the dateline or prime
  meridian.
- **Latitude direction.** Many products run north-to-south; a plot that flips
  or a slice built with an ascending assumption silently returns nothing.
- **Computing area or distance in degrees.** A degree of longitude is not a
  fixed distance. Reproject to an equal-area projection for areas, or use
  geodesic functions; never take Euclidean distance on lat/lon.
- **Buffering, intersecting or centroiding in a geographic CRS.** These
  operations assume a planar metric. Reproject first.
- **Reprojecting a categorical raster with bilinear resampling.** Land-cover
  class 3 averaged with class 7 is not class 5. Use nearest neighbour for
  categories.

## Choosing a projection

- Area statistics (land area, burned area, population totals): equal-area, such
  as an Albers or Lambert azimuthal equal-area centred on the region, or an
  equal-area global projection.
- Distance and buffering: a local projected CRS (UTM zone, national grid) or
  geodesic computation.
- Display: whatever communicates, but label it. Web Mercator distorts area
  severely at high latitude and must not be used for area statistics.

## Vector and raster together

Reproject the *vector* to the raster's CRS for zonal statistics, not the other
way round — resampling the raster changes the values you are about to
aggregate. Check that geometries are valid and that polygons do not cross the
antimeridian unrepaired.

## Verification

Overlay a coastline or a known point (a city, a gauge station) and confirm it
lands where it should. Recompute a known area (a country's land area) and
compare with a published figure. State the CRS, resampling method and any datum
transform in the methods section — a figure without a stated projection is not
reproducible.
