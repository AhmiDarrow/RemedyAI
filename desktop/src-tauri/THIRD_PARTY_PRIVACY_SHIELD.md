# Privacy Shield — third-party notices

Remedy’s in-app **Browser Privacy Shield** uses:

## Engine

- **Brave adblock-rust**  
  https://github.com/brave/adblock-rust  
  License: **Mozilla Public License 2.0 (MPL-2.0)**  
  Source for the crate is on crates.io / the GitHub repo. Modifications to MPL-covered files (if any) will be offered under MPL-2.0.

## Filter lists

- **EasyList** and **EasyPrivacy**  
  https://easylist.to/  
  Dual licence: **GNU GPL v3 (or later)** *or* **Creative Commons Attribution-ShareAlike 3.0**  
  Attribution: *The EasyList authors (https://easylist.to/)*  
  Remedy uses the lists as **data** under the **CC-BY-SA 3.0** option. They are
  not compiled or linked into the app, so the GPL option does not apply to
  Remedy itself. Share-alike, if you modify and redistribute a list, applies
  to that list file, not to Remedy.

Lists are downloaded to `~/.remedy/privacy-shield/` on first use and refreshed about every 3 days (or via **Update lists** in Settings).

## Not included

- **uBlock Origin** (GPL-3.0) is **not** vendored or linked.  
  For full uBO, open pages with **↗ system browser** and install uBO there.
