# Ecology and field survey data

## Detection is never perfect

The central problem in field ecology is that "not seen" is not "not there".
Design and analysis must estimate detection probability rather than assume it:

- **Occupancy models** — repeat visits to the same sites within a season of
  closure separate occupancy from detection. Report number of sites, visits per
  site, naive occupancy and estimated occupancy.
- **Distance sampling** — line or point transects with measured detection
  distances fit a detection function to estimate density. Assumes objects on the
  line are near-certainly detected, distances are measured accurately, and
  animals do not move in response to the observer.
- **Mark-recapture** — capture histories estimate abundance and survival. State
  the model family (closed vs open population), the assumptions about marks and
  capture heterogeneity, and the number of recaptures — a handful of recaptures
  gives an estimate with an interval too wide to use.
- **Counts without detection modelling** are indices, not abundance. Say
  "index of relative abundance" and only compare across places or times where
  effort and conditions are comparable.

## Effort and sampling design

Record effort explicitly: transect length, time on station, trap-nights, net
hours, observer identity, weather, time of day, season. Effort correlates with
count and with habitat, and unrecorded effort is unrecoverable later.
Randomise or stratify site selection; roadside and accessible-site samples are
biased toward disturbed habitat. Pilot to size the survey and use
`power_analysis` with the effect worth detecting, remembering that overdispersed
counts carry less information per sample than normal theory assumes.

## Analysis

Counts are usually Poisson or negative binomial with excess zeros;
`data_profile` reports the zero fraction and the variance/mean ratio, and
`stats_assumptions` will flag zero inflation. Site, observer and year are
grouping factors — treat them as random effects rather than pooling.
Spatial autocorrelation between nearby sites inflates significance; model it or
report the check.

## Taxonomy and vouchering

Record the identification, the authority and the taxonomic backbone used (for
example GBIF or Catalogue of Life), with the access date, because names change.
Voucher specimens or photographs and their accession numbers make an
identification checkable; without them, a rare-species record is unverifiable.

## Permits, welfare and data sharing

Field work needs land access permission, collection permits, and animal-ethics
approval for handling, marking or invasive sampling. Protected-species and
nest-location records are commonly withheld or coarsened before publication to
prevent targeted harm — generalise coordinates and say that you did. Submit
occurrence records to a repository with the Darwin Core fields the archive
requires, and cite the dataset DOI when reusing one.
