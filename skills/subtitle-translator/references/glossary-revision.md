# Applying a glossary to existing translations

Use the supplied project's choices even if another localization uses different terms. Preserve the supplied glossary; a local copy can serve as the active reference. Do not label its provisional names as verified official translations.

1. Identify current translated outputs, their original masters, translation maps, and any manual edits. After filename changes, resolve actual current filenames before editing. Preflight all destinations.
2. Compare the requested glossary with actual translated text. Work with source-to-target mappings and context, not an indiscriminate global replacement. Apply longer overlapping names before short forms; preserve distinct full designations and aliases unless the glossary covers them.
3. Keep a recoverable backup or separate revised outputs. Prefer a narrow batch: preserve source files, timecodes, ASS fields, styles, and original bilingual language. Avoid editing both language halves or comments/headers via whole-file string replacement.
4. Revise the translated field. Check grammatical consequences: a disease noun cannot always replace a verb, and a person label may need a word such as “patient.” Review affected cues with neighboring dialogue. A corrected term is not a full retranslation or a new AV review.
5. Update saved translation maps after checking they match the current output. If the map differs, reconcile the actual edits; do not blindly rebuild and overwrite a more recent subtitle. With the optional helper, merge only the revised cues using `fill --replace`, then build a new output with `--glossary` to flag remaining gaps. Record the active mappings clearly in work context, retaining only useful historical decisions.
6. Validate all affected files against original baselines and before/after snapshots. Scan for superseded terms only in current output scope; backups legitimately retain them. Log file, cue ID/time, old/new text, and changed-cue totals. Count changed cues separately from replacement occurrences.

Leave unlisted terms unchanged unless translation meaning requires a correction or the user requested broader editing. Report unresolved mapping conflicts outside the subtitles. On rename-only tasks, don't perform a hidden terminology revision.
