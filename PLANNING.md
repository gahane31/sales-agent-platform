# Planning

This is for a salesperson at a cybersecurity company. They already have a list of companies that show up in internet scan data. The app turns that list into something they can work through: which companies look worth looking at first, what we know about them, and what we found on their public-facing systems.

## Priority companies

The main screen is a company list, sorted by a score. Higher score means the company is higher priority.

One account is one domain. We skip hosting networks, CDNs, and ISPs, because those are not the customer. If one company has two domains, we keep both. The scan data does not tell us which domain is the legal company, and merging them would hide the second domain.

The score is just a calculation. It is not written by the model.

- Need comes from the security findings and the weight we gave each finding.
- Fit comes from the industry, the type of business, and whether the host looks like a real company instead of a cloud or CDN edge.
- Timing goes up when there is a reason to look at the company now, including a known exploited vulnerability.
- Reach goes up when there are more hosts and services, and when we have a recognizable company name.

The final score is 0.40 need, 0.20 fit, 0.25 timing, and 0.15 reach.

Tier A and tier B are the companies shown by default. Tier C and tier D stay in the database, but they are not on the default list.

The model does not decide this score. If we want to change how important a finding is, we change the weight in `signal_catalog` and run the score again.

## Company page

When someone opens a company, they should be able to see two things without going somewhere else.

The first is a short company overview. It is a description of what the business does, written from a web search and saved on the account. This is so the security findings are not the only thing on the page. We cache the search for that account.

The second is the signal findings. These come from the scan, not from the model. Examples are a known exploited vulnerability, an open database, an admin login, a weak certificate, and the other types in the catalog. The wording we are willing to show is stored in the signal catalog table.

The model writes a short summary that connects those findings to the company overview, plus one line for each finding. It does not choose the title, the priority, the evidence line, or the product area. After the model replies, we set the priority from the finding's weight: high at 70 or above, medium from 40 to 69, low from 1 to 39, and informational at 0. The model is not allowed to add a host, a CVE, or a false claim like "breached".

Outreach is a short first note. We only write it after the overview exists. It uses the overview and the findings. It is not another score.

## What a user can change

A user can set a status, leave a note, and mark feedback. These do not change the score. They are there so the next time the list is opened, it is easy to see what was already done.

Status starts as new, and can move to contacted, replied, or meeting. A company we already reached out to is then not treated like a new one. Closed and disqualified companies stay in the database and come off the list.

A note is a short reminder of who was spoken to or what was said.

Feedback is for a company that should not be on the list: the wrong company, not our kind of customer, already a customer, or bad evidence. It leaves the list, and the row stays so we still know why.


## Why only signal insights is evaluated

The score, the labels, and the priority do not need a model. The part that can go wrong is the summary and the lines on each finding, because that text is what a salesperson might say. That is the skill we wrote a prompt for and the one we run evals on.
