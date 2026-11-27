# Regridding, weighting and masks

## Choose the method by what must be preserved

- **Nearest neighbour** — categorical fields (land cover, ecoregion, flags).
  Never interpolate a category.
- **Bilinear / bicubic** — smooth continuous fields for display or point
  extraction. Does not conserve integrals and smooths extremes.
- **Conservative (first or second order)** — anything that must integrate
  correctly: precipitation, radiative and turbulent fluxes, emissions, areas,
  population counts. Requires cell bounds on both grids.
- **Patch / higher-order** — smoother gradients where derivatives matter.

Coarsening by simple averaging is only correct on an equal-area grid. On a
lat/lon grid, weight by cell area, and for many fields also by the valid-data
mask.

Tools: `cdo remapcon/remapbil/remapnn`, xESMF, `gdalwarp -r`, `nco`. Whichever
runs, record the exact command in the ledger through `analysis_run`.

## Regrid once, and downward

Every regrid loses information. Do it at one documented step, keep the source
grid file, and prefer regridding the finer field to the coarser grid rather than
inventing resolution. Upsampling to a fine grid does not add information and
makes the result look more certain than it is.

## Area weighting

A global or regional mean over a lat/lon grid needs `cos(latitude)` weighting
(or true cell areas from the bounds). Without it, polar cells are overweighted
by an order of magnitude and warming trends are overstated. Use the tool's own
area variable (`areacella`, `areacello` in CMIP) when it exists — it accounts
for the actual grid, including curvilinear ocean grids.

Weighted mean with missing data: renormalise by the sum of weights over valid
cells only, or the missing cells silently count as zero.

## Masks

- Land/sea masks belong to a specific grid. Applying an ERA5 mask to a model
  grid puts coastline cells in the wrong category. Regrid the mask
  conservatively and threshold, or use the product's own mask.
- Fractional masks (0-1 land fraction) are not binary; decide the threshold and
  say it, or carry the fraction as a weight.
- Sea ice, glacier and quality masks change over time — a static mask applied to
  a long record creates a spurious trend.
- Ocean model grids are often curvilinear (tripolar); their `lat`/`lon` are 2-D
  and cannot be sliced by index ranges.

## Verification

- **Conservation check**: after a conservative regrid, the area-weighted global
  integral of the field matches the source within tolerance. Compute the
  residual and report it — this is the standard proof that the regrid was done
  right.
- **Coastline check**: plot the mask boundary against a coastline dataset.
- **Range check**: min/max should not exceed the source range for a conservative
  or bilinear remap; if it does, something interpolated across a fill value.
- Run the check as part of the pipeline so `analysis_ledger` records it, not as
  a one-off in a notebook you will lose.
