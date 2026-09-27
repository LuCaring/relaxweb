# Playing card SVG artwork

The 52 traditional playing card faces with medium corner indices and one back used by the desktop Holdem remaster are by **Adrian Kennard**. The author releases these designs under **CC0 Public Domain** and states that attribution is not required.

- Source: https://www.me.uk/cards/
- Plain deck with medium indices: https://www.me.uk/cards/makeadeck.cgi?view&ace=Plain&ace1=&ace2=&qr=&super=1
- Downloaded: 2026-09-27
- Licence: https://creativecommons.org/publicdomain/zero/1.0/
- Offline legal text: `CC0-1.0.txt` (downloaded from https://creativecommons.org/publicdomain/zero/1.0/legalcode.txt)

`deck.ts` contains the original self-contained SVG markup for 53 selected cards, embedded in the application bundle so entering a room does not require another network request. The runtime only changes SVG instance IDs and their internal references to prevent collisions when multiple copies of the same card are on the table. The source drawing paths and layout are left intact.
