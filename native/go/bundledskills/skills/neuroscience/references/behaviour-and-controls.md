# Behaviour as the anchor, and the controls

A neural difference is only interesting relative to something the animal or
person did or saw. Collect and report behaviour at the same resolution as
the neural data: accuracy, reaction time, bias, eye position, pupil,
movement, running speed, arousal state.

## Task design

- Conditions must differ in the variable of interest and not in the obvious
  nuisances: difficulty, reaction time, trial count, luminance or contrast,
  motor demands, time on task, reward rate. A "memory" contrast that also
  differs in difficulty is a difficulty contrast.
- Counterbalance order and stimulus assignment; randomise trial order with
  a stored seed.
- Fatigue, learning and drift over a session are real: model time on task or
  interleave conditions.
- Eye and micro-movements drive visual and motor areas and contaminate EEG;
  record them, then regress or exclude on a pre-specified rule.
- Arousal and locomotion modulate rodent cortex broadly; a pupil or running
  regressor keeps state differences from posing as task effects.

## Controls by method

- **Optogenetics**: opsin-negative animals with identical light delivery;
  fluorophore-only and light-only controls; verified expression extent and
  measured irradiance; sham fibre placement for implant effects; awareness
  that stimulation spreads antidromically and downstream.
- **Chemogenetics**: a ligand-only control in non-expressing animals — the
  ligand and its metabolites act on their own.
- **Lesion / inactivation**: sham surgery, a control site of similar size,
  acute inactivation where possible (permanent lesions are confounded by
  compensation), and histological verification of extent for every animal,
  excluded ones included.
- **Pharmacology**: vehicle control, dose-response, timing vs behaviour.
- **Stimulation (TMS/tES)**: an active control site and sham with matched
  sensation, plus a blinding check.

## Animals: reporting and approval

Work runs under institutional animal-care approval (IACUC or the local
equivalent) and the applicable welfare regulation; the approval identifier
and the humane endpoints belong in the methods. Report species, strain, sex
(both, unless justified), age, weight, housing, light cycle, and any
water/food restriction with its welfare monitoring. Report every animal:
enrolled, excluded, why. ARRIVE (via `manuscript_check`) covers the
reporting items — check its current version at the EQUATOR Network rather
than quoting item numbers from memory.

## Rigour at the bench

Randomise animals to groups by a documented method; blind the experimenter
during collection and scoring, and the analyst until the primary analysis is
locked; pre-specify exclusions and apply them before looking at the effect;
state the unit of analysis (animal, session, cell) and keep the statistics
there.

Human recordings carry consent, ethics approval and de-identification as
part of the procedure — activate `clinical-research` for patient studies.
