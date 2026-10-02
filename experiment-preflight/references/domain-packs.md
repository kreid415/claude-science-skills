# Writing a domain pack for experiment-preflight

A pack adds domain-specific items (imaging, NLP, single-cell, molecular simulation...) that gate GO exactly like generic items. The generic skill never imports the pack; the user registers it with `extra_items=`.

## Contract

1. Skill `experiment-preflight-<domain>` with `kernel.py` exposing `<prefix>_items()` (prefix `sc_`, `img_`, `nlp_`) -> list of dicts with exactly these keys:
   `id` (`SC-01`...; never `PF-`), `area`, `question`, `severity` (`blocking|major|minor`), `applies_if`, `how_to_check`, `auto` (name of a helper in the pack kernel or `None`).
2. Auto helpers: `<prefix>_check_<name>(real_inputs..., strict=True)`; build the result with `pf_result(item_id, check_name, ok, summary, **details)` and end with `return pf_finish(result, strict, "<Name>Error")` so failures raise `pf_exc("<Name>Error")`. These functions live in the generic kernel; the pack calls them by bare name (load `experiment-preflight` first) or checks `pf_result` exists and raises a clear error otherwise.
3. Validate in the pack's tests: `pf_validate_items(<prefix>_items())` returns True.

## Registration (user side)

```python
rec = pf_new("12_run", plan, extra_items=sc_items())
pf_record_check(rec, "SC-03", sc_check_species(var_table, allowed=["human"], strict=False), extra_items=sc_items())
v = pf_verdict(rec, extra_items=sc_items())
```

Registered extra items are stored in `preflight.json`, so `pf_load` + `pf_verdict(rec)` keeps working without re-passing them. Answers for ids that are not registered raise `InputError`, which prevents a typo or an unregistered pack from silently dropping gating items.

## Skeleton

```python
def img_items():
    return [{"id": "IMG-01", "area": "Patient-level split", "severity": "blocking",
             "question": "Are all slides/tiles of one patient on one side of every split?",
             "applies_if": "Tiles or slices are classified or segmented.",
             "how_to_check": "Map tile -> patient and run pf_check_grouped_split.", "auto": None}]
```

Prefer reusing generic helpers (grouped split, coverage, identity baseline, manifest, cost) over reimplementing; domain packs should add only what is domain-specific (e.g. species namespaces for genes, tokenizer/licence filters for text, stain-batch confounds for images).
