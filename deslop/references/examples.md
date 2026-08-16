# Before and after, by document type

Every example preserves the original's numbers, hedge strength, and citations. Where the slop version hid a missing fact, the repair names the gap rather than inventing a value.

## Abstract, motivation sentence

Before:

> In recent years, machine learning has garnered significant attention across a myriad of biomedical domains, playing a crucial role in advancing our understanding of complex disease processes.

After:

> Polygenic risk scores predict breast cancer incidence well in European-ancestry cohorts and poorly elsewhere, and the reasons for the gap are unresolved.

Changed: replaced a sentence that could preface any paper with the specific unresolved problem. Cut "in recent years", "garnered significant attention", "a myriad of", "playing a crucial role", "complex disease processes".

## Abstract, results sentence

Before:

> Our approach achieved substantially better performance than existing methods, demonstrating the effectiveness of the proposed framework.

After:

> Transfer accuracy rose from 0.61 to 0.78 AUROC in the African-ancestry test set (n = 4,192), while remaining unchanged in the European-ancestry set.

Changed: numbers replaced "substantially better"; the participial after-thought went; the trade-off the slop version omitted is now stated.

## Introduction, literature framing

Before:

> Researchers have shown that batch effects can be problematic. It has long been recognized that normalization is important, and a growing body of literature underscores the need for careful preprocessing.

After:

> Batch effects can dominate biological signal in scRNA-seq (Tran et al., 2020), and no single normalization performs best across protocols (Luecken et al., 2022).

Changed: two vague attributions became two citations; "underscores the need for" and "growing body of literature" deleted; the second clause now makes a claim rather than gesturing at one.

## Introduction, gap statement

Before:

> While much progress has been made, several challenges remain. This is not a purely technical problem, it is a conceptual one. What is needed? A principled framework.

After:

> Existing correction methods assume that batch and cell type are independent. In our data they are confounded by design, because each donor was processed on a single day.

Changed: cut the vague progress sentence, the binary contrast, and the rhetorical question. The "principled framework" claim is replaced by the specific assumption that fails.

## Methods, passive voice left alone

Before:

> Libraries were prepared using the Chromium 3' v3.1 kit and sequenced on a NovaSeq 6000 to a median depth of 42,000 reads per cell.

After: unchanged.

Why: Methods passives are correct. The kit and instrument are the subject of interest, and naming an agent ("A technician prepared...") adds nothing. Do not convert these.

## Results, false agency

Before:

> Our analysis reveals that donor identity drives clustering, underscoring the importance of accounting for donor effects.

After:

> Cells clustered by donor before correction (ARI 0.44 against donor, 0.11 against cell type; Fig. 2a). After Harmony, the ordering reversed (0.09 and 0.51).

Changed: "our analysis reveals" became what we observed; the empty importance clause was replaced by the numbers that justify it.

## Discussion, implication

Before:

> It's worth noting that these findings have important implications for how we navigate the challenges of single-cell integration moving forward. Despite these challenges, this work contributes meaningfully to the field, highlighting the need for continued evaluation.

After:

> If donor and cell type are confounded by processing day, integration methods that assume independence will remove biological signal along with the batch effect. We observed this for two of the four methods tested (Fig. 4), and we did not test whether pooling donors within a day avoids it.

Changed: named the implication and its mechanism, cut four filler constructions, and converted "the need for continued evaluation" into the specific untested question.

## Discussion, limitations

Before:

> As with any study, our work has certain limitations. Future work should explore additional datasets and further validate these findings.

After:

> All four datasets used 10x 3' chemistry, so we cannot say whether the confounding we describe affects plate-based protocols. Our donor count (n = 6) also leaves the day-versus-donor contribution unidentifiable.

Changed: replaced the boilerplate with two limitations that a reader can act on, each naming what it prevents you from concluding.

## Cover letter

Before:

> We are excited to submit our manuscript, which we believe represents a significant advance in the field and will be of broad interest to your readership. This work sheds light on a critical gap and paves the way for future studies.

After:

> We submit "Donor-day confounding in single-cell integration" for consideration as an Article. The manuscript shows that three widely used integration methods remove cell-type signal when donors are processed on separate days, a design used in most public atlases. We expect it to matter to readers who build or reuse integrated references.

Changed: cut the enthusiasm and the two trope verbs; stated what the paper shows and who it affects.

## Reviewer response

Before:

> We thank the reviewer for this insightful comment. We have carefully considered this point and have addressed it in the revised manuscript. We believe the manuscript is now significantly improved.

After:

> The reviewer is right that we did not test plate-based data. We added the Smart-seq2 dataset from Zhang et al. (2021) as a fifth benchmark (new Fig. S6). The confounding persists there (ARI against donor 0.39 before correction), which we now state in the Discussion, p. 14, lines 3-9.

Changed: replaced gratitude and a claim of improvement with the concrete change, the new evidence, and the location in the revision.

## Grant specific aims

Before:

> This project will leverage cutting-edge computational approaches to unlock a deeper understanding of the intricate mechanisms underlying integration failure, ultimately paving the way for transformative improvements in reproducibility.

After:

> Aim 2 will test whether donor-day confounding explains integration failure, by processing 12 donors in a crossed design (each donor split across two days). If confounding is the cause, correction accuracy should recover when the design is crossed and stay degraded when it is nested.

Changed: cut five trope constructions; stated the hypothesis, the design that tests it, and the prediction that would falsify it.

## Figure caption

Before:

> Figure 3: Comparison of integration methods, demonstrating the superior performance of our approach and highlighting the importance of donor-aware correction.

After:

> Figure 3: Integration accuracy by method. Bars show ARI against annotated cell type (mean of 5 seeds, error bars 95% CI, n = 18,442 cells). Asterisk: p < 0.01, paired Wilcoxon against Harmony.

Changed: removed interpretation verbs and replaced them with what the panel shows, the statistic, the n, and the test.

## Slide bullet

Before:

> - **Key insight**: Donor effects fundamentally reshape how we think about integration.

After:

> - Donors processed on separate days: correction removes cell-type signal (ARI 0.51 to 0.09)

Changed: dropped the bold lead and the stakes inflation; the bullet now carries the finding. Fragments are fine on slides.

## README

Before:

> This package provides a seamless, robust solution for effortlessly navigating the complexities of single-cell integration diagnostics.

After:

> Diagnoses donor-day confounding in scRNA-seq before integration. Reports ARI against donor and cell type, and flags nested designs. Requires scanpy >= 1.10.

Changed: replaced three jargon adjectives with what the package does, what it reports, and what it needs. Second person and imperative are acceptable here.

## General prose (blog)

Before:

> Here's the thing: most bioinformatics pipelines break in production. Not because the code is bad. Because the data is bad. Let that sink in.

After:

> Most bioinformatics pipelines break in production. The code usually runs fine. The failure is that the incoming data stops matching the assumptions the pipeline was written against, and nothing checks.

Changed: cut the opener, the binary contrast, and the emphasis crutch. The vague "data is bad" became the specific failure mode.
