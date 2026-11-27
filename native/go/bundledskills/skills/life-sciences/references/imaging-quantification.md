# Microscopy, blots and honest quantification

## Acquire so it can be quantified

- Identical settings across every condition compared: exposure, gain, laser
  power, pinhole, objective, binning, z-step. Keep the raw vendor file
  (.czi, .nd2, .lif, .oib), which carries them in metadata, and export from
  it — never quantify a JPEG.
- Check for **saturation** first. A saturated pixel is censored, not bright.
  Use the histogram or a saturation LUT; if the positive control saturates,
  the dynamic range is wrong for the experiment.
- Image the same number of fields per unit, chosen by a rule (systematic
  random positions, a stage grid), not by hunting for good ones.
- Collect a secondary-only and an unstained field per session.
- Flat-field/dark-frame correct uneven illumination, and say whether you did.

## Adjustment: what is allowed

Linear, whole-image, applied identically to every image in the comparison and
declared in the legend: brightness/contrast levels, a single stated gamma.
Not allowed without saying so explicitly: local adjustment, cloning, erasing,
non-linear filtering before quantification, splicing panels without a
divider, cropping that removes context. Quantify the raw data, adjust only
the displayed figure, keep both.

## Segmentation and thresholds

The threshold is a decision, and it is the commonest source of an effect that
only exists in one person's hands.

- Fix the method before seeing the arms: a named automatic method (Otsu, Li,
  triangle) applied identically, or a manual threshold set blind on coded
  files.
- Record the pipeline: software and version, filter radii, threshold method,
  size and circularity exclusions, watershed settings. A Fiji macro or a
  CellProfiler pipeline file is the record — save it with the data.
- Report objects and fields alongside the number of biological units.
- Tools people actually use: Fiji/ImageJ, CellProfiler, QuPath (histology),
  Ilastik and Cellpose/StarDist (learned segmentation). Learned models need
  their training/validation set described.

## Colocalisation

Report Pearson and Manders coefficients with the thresholds used, plus a
control for random overlap (rotate or shift one channel and recompute). Check
bleed-through with single-stain samples and register the channels; chromatic
shift alone can create apparent colocalisation.

## Blot densitometry

- Quantify the raw 16-bit scan, never a saved figure image.
- Subtract local background per lane; state the method (rolling ball radius).
- Confirm the exposure is not saturated — film is non-linear; digital imagers
  report saturation.
- Normalise to a loading control shown to be unchanged by the treatment, or
  to a total-protein stain (Ponceau, stain-free), and say which.
- Show the whole membrane with the ladder in the supplement. Any splice needs
  a visible line and a caption.
- Blots are semi-quantitative. Independent membranes are the biological
  replicates, and small differences are not real.
