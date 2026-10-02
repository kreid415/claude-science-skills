# Prior-work (novelty) check: tiered protocol

The goal is to learn early whether a method, metric, analysis, or claimed contribution already exists, so the project can
cite and differentiate it or change direction. The output is never "this is novel". It is a scoped statement:
"No prior work matching components X, Y found in sources S for queries Q (N screened, date)", or a list of overlapping works
with the specific shared and differing components.

## 1. Method card
Write or update the method card before searching. Fields: `problem`, `approach`, `components` (list, each one separately
searchable), `data`, `evaluation`, `claimed_contribution`. Most overlap is partial: a prior paper shares the problem and
two of four components. Separating components makes that visible.

## 2. Query formulation (3–5 queries per card; record all of them)
- Task vocabulary: what the problem is called in this field.
- Method vocabulary: what the technique is called in the field it came from (statistics, ML, physics, signal processing).
  Reinvention often hides behind a different name; e.g. a single-cell "metric" may exist as an information-theoretic
  measure in the ML disentanglement literature.
- The most distinctive component on its own, without the application domain.
- One query naming the closest known prior work's terminology, if any.
Use short keyword queries (the arXiv adapter ANDs the first four words longer than three characters).

## 3. Quick tier (every review that touches a method)
`novelty_search(queries, since_year=None)` over OpenAlex + arXiv + Europe PMC (PubMed and bioRxiv/medRxiv preprints), then
`rate_overlap(card, hits, top_n=40)`. Treat the LLM screen as triage. Read the abstracts of everything it rates `partial`
or `substantial` yourself before recording anything. If OpenAlex was skipped (no key), the coverage statement says so.

## 4. Escalation tier
Escalate when any hit is `partial`/`substantial` after your own reading, when the card is new to the ledger, or when the
output makes a "first"/"novel" claim. Steps:
1. Fetch full text of the 3–5 closest works (`fetch_article_fulltext` by DOI; arXiv PDF otherwise). Compare component by
   component against the card; quote the passage (under 20 words) that establishes each overlap.
2. Expand the citation graph of the closest one or two works: `citation_neighbors(openalex_id)` for backward references and
   forward citations; screen those with `rate_overlap`.
3. Optionally search code (GitHub) and benchmark venues (NeurIPS Datasets & Benchmarks, workshop papers) for unpublished
   or preprint-only implementations.

## 5. Verdicts (record in the method card search entry)
- `no match in scope`: nothing beyond same-area work after escalation. State the scope.
- `partial overlap`: list the works and which components overlap; recommend citing them and naming the differentiating component.
- `substantial overlap`: the contribution as stated exists. Severity `fatal` if the project's main claim depends on it,
  otherwise `major`. Recommend options: reframe as replication or extension, change the contribution, or compare directly.
- `unchecked` / `screen only`: the escalation tier was not run; say so wherever the verdict is reported.

## 6. Re-checks
Re-run the quick tier for each card when its last search is older than ~2 months, before submission, and whenever the
card changes. Restrict re-checks to `since_year` = year of the last search to see new work only.
