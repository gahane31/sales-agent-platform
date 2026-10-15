# How I built this

I started by trying to understand how a sales team picks which companies to look at, and what this scan data can actually say. I did not want to hand the raw scans to a model and ask it for a customer list. The early work was looking through the fields. I used Cursor for that exploration as well as for the code. From the scans we can derive things ourselves: a certificate that is expiring, a known exploited vulnerability, old software, weak encryption, and services left open on the internet. The useful part was turning those observations into signals we can sort companies by.

Once that was clear, the scoring stayed in rules. If a known exploited vulnerability is present, its priority comes from the weight we set and from the evidence, not from asking the model how serious it is. The company list is ordered by that calculated score. The model is there for the part a salesperson has to read: a short description of the company, an explanation of the findings we already have, and a first note for outreach.

Those are three separate jobs, not one agent that does all of it. The overview says what the company does. The signals job connects those findings to that overview. It is not allowed to add a finding, a host, or a claim the scan did not support. Outreach is written only after the overview exists, from the overview and the findings. Splitting them made each output easier to check and change.

I also wanted the cost and the checks on a page, not only in a log. The system page shows what the jobs and the eval runs spent, and it compares the latest signal-insights run with the previous one, including how many checks passed. A prompt change can improve one sentence and make another worse. The comparison is there so that is not judged from a few replies I happened to open.

The line I kept coming back to is that the data and the wording are different. A vulnerability on a company's address is not the same as saying the company was breached. Not seeing a firewall is not the same as saying there is no firewall. The model will overstate that if the prompt leaves room for it. The eval is there so the text stays tied to the evidence we sent in.

Cursor saved the most time after those rules were clear: the scan tables, the list, saving each model call, and the eval run. It cost more when the instruction was shorter than the rule. "Use the page title as the company name" copied one page onto other domains that only shared an address. Narrowing that rule, and running the names again, took longer than the first version.

The weakness I would flag is the same kind of mistake, still in the score. We stopped copying that page title onto every domain. A scan on a shared address can still attach another site's findings to those domains, and the score treats them as that company's. The name can look right while the order of the list is still off. Wider eval coverage would help the wording. It would not fix that.
