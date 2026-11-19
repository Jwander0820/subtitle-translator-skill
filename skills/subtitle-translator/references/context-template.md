# Work context template

Copy into `subtitle-context.<target-locale>.md` beside the task files. Keep each section short; replace entries rather than appending history. Move per-cue changes to a separate revision log.

```markdown
# <Work title> (<edition/season>) → <target-locale>

## Identity
- Work / edition: <title, season, release or distributor>
- Source language and track: <e.g. ja, SDH, ASS>
- Source provenance: <supplied / extracted text / ASR; for ASR, the run directory and model>

## Presentation
- Layout: <translation only | translation above source | source above translation>
- Tone and register: <e.g. casual, keep honorifics>
- Locale conventions: <punctuation, quotes, numerals, line length>
- Output naming: <pattern, if different from the default>

## Names and terms (active)
| Source | Target | Basis | Notes |
| --- | --- | --- | --- |
| <name or term> | <translation> | glossary / official / provisional | <alias, scope, common ASR misspellings> |

## Superseded mappings
- <old> → <new> (<date>, <reason>)

## References
- <URL> (<date checked>): <what it established>

## Unresolved
- <episode/cue>: <open question>
```
