# Genre → Playlist classifier map

Two rule types: **exact match** and **contains**. Always check exact matches first, then fall through to contains rules. If nothing matches, return `None` and the caller skips the track.

## Exact matches (case-insensitive, trimmed)

| Beatport genre              | Destination playlist |
|-----------------------------|----------------------|
| Dance/Pop                   | Dance                |
| Melodic House & Techno      | Melodic House        |
| House                       | House                |
| Tech House                  | Tech House           |
| Indie Dance                 | Indie Dance          |
| Drum & Bass                 | DnB                  |
| Techno (Raw / Deep / Hypnotic) | Hypnotic Techno   |
| Techno (Peak Time / Driving)   | Peak Techno       |
| Downtempo                   | Downtempo            |
| Progressive House           | Progressive House    |
| Bass House                  | Bass House           |
| Afro House                  | Afro House           |
| Deep House                  | Deep House           |
| Hard Techno                 | Hard Techno          |
| Ambient                     | Ambient              |
| Electronica                 | Electronica          |

## Contains rules (case-insensitive substring match on the genre string)

Apply these only if no exact match was found. Order matters — check more specific terms first.

| If genre contains | Destination playlist |
|-------------------|----------------------|
| `dubstep`         | Dubstep              |
| `mainstage`       | Mainstage            |
| `minimal`         | Minimal              |
| `trance`          | Trance               |

## Lookup pseudocode

```python
EXACT = {
    "dance/pop": "Dance",
    "melodic house & techno": "Melodic House",
    "house": "House",
    "tech house": "Tech House",
    "indie dance": "Indie Dance",
    "drum & bass": "DnB",
    "techno (raw / deep / hypnotic)": "Hypnotic Techno",
    "techno (peak time / driving)": "Peak Techno",
    "downtempo": "Downtempo",
    "progressive house": "Progressive House",
    "bass house": "Bass House",
    "afro house": "Afro House",
    "deep house": "Deep House",
    "hard techno": "Hard Techno",
    "ambient": "Ambient",
    "electronica": "Electronica",
}

CONTAINS = [
    ("dubstep", "Dubstep"),
    ("mainstage", "Mainstage"),
    ("minimal", "Minimal"),
    ("trance", "Trance"),
]

def classify(genre: str) -> str | None:
    if not genre:
        return None
    g = genre.strip().lower()
    if g in EXACT:
        return EXACT[g]
    for needle, playlist in CONTAINS:
        if needle in g:
            return playlist
    return None
```

## Notes on fuzzy matching

The user noted that left-side values "might not be exactly the same" — Beatport occasionally renames sub-genres or includes extra qualifiers. If a genre is close to an exact-match key but not identical (e.g. "Melodic House and Techno" with the word "and" instead of `&`), normalize before lookup:

- lowercase
- replace `&` with `and`, then back to `&` (or normalize both directions and check both)
- collapse multiple whitespace
- strip surrounding punctuation

Don't over-fuzz. A track whose genre is unrecognizable to the map is better skipped and logged than misrouted.
