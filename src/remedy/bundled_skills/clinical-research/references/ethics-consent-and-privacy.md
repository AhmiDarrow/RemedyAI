# Ethics, consent, and patient data

Governance is part of the method: it belongs in the procedure steps and the
methods section, not a footer.

## Review before work starts

Human-subject research needs prospective IRB/REC approval, or a documented
exemption from that body — the investigator does not decide their own
exemption. Secondary analysis of existing data usually still needs a
determination plus a data-use agreement. Record the approval number and the
approving body; both go in the manuscript.

Never draft material whose purpose is to mislead a review board or to run an
intervention outside an approved protocol. Name the process (IRB/REC,
sponsor, regulator) and stop.

## Informed consent

Ordinarily: purpose, procedures, duration, foreseeable risks and benefits,
alternatives, confidentiality limits, compensation and injury provisions,
voluntariness and the right to withdraw without penalty, whom to contact,
and — for tissue or data — future use and sharing. Readable at the
population's reading level, in their language, with time to decide.

Waiver or alteration of consent is a board decision (minimal risk,
impracticability, no adverse effect on rights). Broad consent for future use
must state what "future use" covers.

## Vulnerable populations

Children (assent plus parental permission), impaired decisional capacity
(legally authorised representative, reassessment), pregnant people and
fetuses, prisoners, employees and students (undue influence), disadvantaged
participants, and emergency settings (exception from informed consent, with
community consultation). Each carries extra protections in the governing
regulations — the board says which apply; do not improvise them.

## De-identification and handling

- **HIPAA** offers two routes: Safe Harbor (strip the enumerated identifier
  categories — names, geography below state, all dates finer than year and
  ages over 89, contacts, ids, device and biometric identifiers, full-face
  images) or expert determination. Check the current HHS guidance for the
  exact list before relying on it; a limited data set requires a DUA.
- **GDPR** shape: a lawful basis (consent remains an ethics requirement in
  parallel), purpose limitation, data minimisation, storage limits, a DPIA
  for high-risk processing, and rules for transfers outside the EEA.
  Pseudonymised data is still personal data.
- **Minimum necessary**: request the columns and the rows the analysis
  needs, held only as long as the protocol says, encrypted at rest, access
  logged, on the approved system.
- **Never paste identifiable data into a model or an external service** —
  names, MRNs, notes, images with faces or intact DICOM headers, or a
  combination rare enough to single someone out. Work on de-identified
  extracts or on the schema alone.
- Small cells re-identify: suppress counts below the threshold the DUA sets.
- Incidental and secondary findings need a pre-agreed return-of-results
  plan, written into the consent form.
