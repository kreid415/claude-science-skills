# Trope catalog

Grouped by level. Each entry gives the tell, why it fails in scientific prose, and the repair.

## Word choice

**The magic adverb.** "The model quietly outperformed every baseline." Quietly how? The adverb manufactures drama around a number. Repair: give the number.

**Delve and cousins.** delve, unravel, illuminate, embark, traverse, journey. Repair: examine, measure, test, or nothing.

**The "serves as" dodge.** "This dataset serves as a benchmark." It either is a benchmark or is used as one. Repair: "We use this dataset as a benchmark" or "This dataset is the standard benchmark for X (cite)."

**Elevated nothing-words.** paramount, pivotal, crucial, vital, essential, key, critical, significant (without a test), substantial (without a number), meaningful, considerable. Each asserts magnitude while withholding it. Repair: the magnitude.

**Nominalization.** "The utilization of the reagent" is "we used the reagent". "The implementation of the optimization" is "we optimized". Nominalized verbs add syllables and remove actors.

**False precision words.** "approximately exactly", "roughly precisely", "nearly identical" where you have the numbers. Give the numbers.

**Journal-inappropriate register.** unpack, drill down, double-click on, level up, supercharge, turbocharge, table stakes, north star, low-hanging fruit. None of these belong in a manuscript.

## Sentence structure

**The false range.** "Everything from protein folding to climate modelling." A range needs endpoints on a shared axis; these have none. Repair: name the two or three cases you actually mean, or drop the flourish.

**The superficial participle.** ", highlighting its importance", ", underscoring the need for further work", ", demonstrating the utility of our approach". These attach an unearned interpretation to a fact. Repair: delete the clause, or promote it to a sentence with an argument behind it.

**Hedge stacking.** "may potentially suggest that it could possibly be associated with". One hedge, chosen for the evidence. See phrases.md.

**The double negative dressed as nuance.** "not uncommon", "not dissimilar", "not without merit". Repair: common, similar, useful.

**The "while" that hides a claim.** "While some studies report X, our results suggest Y." Which studies? Repair: cite them and state the disagreement plainly.

**Ending on the em dash aside.** Remove the dash and decide whether the aside earns a sentence.

## Paragraph structure

**Fractal summary.** Announce, argue, restate. See structures.md.

**The topic sentence that says nothing.** "Several factors influence performance." Repair: name the factor that mattered most and its effect size, then discuss it.

**The obligatory limitations paragraph that limits nothing.** "As with any study, our work has limitations. Future work should address these." A limitation is specific: what you could not measure, what your design cannot distinguish, what your sample excludes. Name it, and say what it does to your conclusion.

**The "despite these challenges" pivot.** Acknowledges a problem and dismisses it in the same breath. Repair: state the problem's consequence for your claim, then state what remains supported.

**The unnecessary bridge.** A whole sentence spent announcing a topic change. Repair: change topic. Readers track paragraph boundaries.

## Tone

**Stakes inflation.** "This fundamentally reshapes our understanding of X." Almost nothing does. Repair: name the specific belief that should change and for whom.

**Patronizing analogy.** "Think of the genome as a library and genes as books." Your reader has a doctorate in this. Repair: delete. If the audience is genuinely lay (a press release, a public abstract), one analogy is enough.

**False vulnerability.** "We will be honest, this surprised us." Manufactured intimacy. Repair: report the surprise as a result. "The effect reversed sign in the held-out cohort, which we did not anticipate."

**Uncritical enthusiasm about your own work.** "Our novel framework elegantly addresses this long-standing challenge." Repair: state what it does and what it costs. Let the reviewer decide if it is elegant.

**The inspirational close.** "As the field continues to evolve, one thing is clear: X." Repair: end on the result, the limitation, or the next experiment.

## Formatting

**Bold-first bullets.** Every item opening with a bolded keyword. Strong AI signal.

**Unicode arrows and decorative symbols in prose.** Fine in a figure or a code block, not in a sentence.

**Emoji as section decoration.** Never in scientific prose.

**Bulleted Discussion or Introduction.** Signals a draft that was assembled rather than argued. Reviewers read paragraphs.

**Signposted conclusions.** "In conclusion", "In summary", "To sum up" at a paragraph head. The section heading already says so.

**Tables used to avoid writing.** A two-column table of "Challenge / Solution" is usually three sentences of prose in disguise.

## Composition

**Dilution.** The same claim restated in three registers across a page. Keep the strongest statement, cut the rest.

**Citation padding.** Six citations after a claim where two carry it. Cite what supports the specific statement.

**Symmetry for its own sake.** Every subsection the same length, every paragraph three sentences, every section closing with an implication. Real arguments are asymmetric because evidence is.

**The unmotivated comparison.** Benchmarking against methods nobody uses, or reporting every metric to avoid choosing one. Pick the metric that matches the decision, justify it, and report the others in the supplement.

**Hollow novelty claims.** "To the best of our knowledge, this is the first study to X." Only survivable if you searched properly and can say what you searched. Otherwise drop it.

## Science-specific tells

**Significance language without a test.** "Substantially improved", "markedly better" where no comparison was run. Either run it or describe the raw difference.

**Causal verb creep.** Writing "causes", "drives", "leads to", or "determines" from observational data. Repair: "is associated with", "predicts", "co-varies with".

**Mechanism asserted from correlation.** "Expression of X drives proliferation via the Y pathway" when you measured expression and proliferation only. Repair: state the association, then propose the mechanism explicitly as a hypothesis.

**p-value prose.** "Highly significant" (p < 0.05 is not a magnitude), "trending toward significance", "approached significance". Report the p-value and the effect size, and let them stand.

**Sample-size vagueness.** "A large cohort", "a small pilot". Give n.

**Method-as-brand.** Capitalizing and naming a routine analysis to make it sound novel. If it is a linear model, call it a linear model.

**Reference to unshown data.** "Data not shown" for a load-bearing claim. Either show it in the supplement or drop the claim.
