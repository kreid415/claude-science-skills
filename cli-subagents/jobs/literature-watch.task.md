# Task: weekly literature watch

Find papers that appeared since {since} (UTC; "never" = first run, so use the last 14 days up to {now}) on these topics, one per line:

{file:~/agent-inbox/literature-topics.txt}

Use web search and fetch the paper pages. For each new paper give: title, authors (first author et al. is enough), venue or preprint server, date, DOI, and two sentences on what it shows and why it matters for the topic. Only list a paper if you opened its page and saw the DOI there; never write a DOI from memory. If a paper has no DOI yet (a preprint without one), give its URL instead and say so.

Return as the `result` of your closing declaration one markdown string: a dated digest titled "Literature watch {now}", grouped by topic, newest first. After the groups add a section "Searched but nothing new" listing the topics with no new papers, and a section "Could not check" for any page you could not open. Do not pad: fewer, verified papers beat a long list.
