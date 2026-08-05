# Third-party material in this repository

VMManager itself is GPL-2.0-or-later - see [LICENSE](LICENSE). The files below
came from elsewhere and keep their own terms.

## Fonts

Under `vmmanager/assets/fonts/`, all under the SIL Open Font License 1.1, whose
text is beside them in [OFL.txt](vmmanager/assets/fonts/OFL.txt). The copyright
lines are the ones recorded in each font's own metadata:

| Font | Copyright | Upstream |
|---|---|---|
| IBM Plex Sans (variable) | Copyright 2019 IBM Corp. All rights reserved. | <https://github.com/IBM/plex> |
| IBM Plex Mono (Regular, Medium) | Copyright 2017 IBM Corp. All rights reserved. | <https://github.com/IBM/plex> |
| Chakra Petch (Medium, SemiBold) | Copyright 2018 The Chakra Petch Project Authors | <https://github.com/cadsondemak/Chakra-Petch> |

## Operating-system logos

Under `vmmanager/assets/logos/`. These are the marks of the projects they name,
included so a machine shows the right logo offline. They are used to identify
those projects and nothing else; they are not covered by this repository's
licence, and no endorsement is implied.

| File | What |
|---|---|
| `linux.png` | Tux, the Linux mascot, originally by Larry Ewing |
| `cachyos.png` | the CachyOS project logo |

If you own one of these and would rather it were not distributed here, open an
issue and it will be removed.

Logos fetched at runtime rather than bundled come from
[simple-icons](https://github.com/simple-icons/simple-icons) (CC0-1.0) and land
in `~/.cache/vmmanager/oslogos/`. Nothing is downloaded unless the feature is on.

## Icons

The chevrons and check mark under `vmmanager/assets/icons/` are drawn for this
project and are covered by its licence.
