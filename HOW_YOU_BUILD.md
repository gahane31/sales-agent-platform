# How I built this

I used Cursor. I would say what I wanted, usually after we looked at what the code already did. The agent made the change, and then I checked the database. I looked at counts: how many companies are on the list, how many share a name, and whether the overviews we already had were still there after a rescore. If something was wrong, we wrote down the number and why, and changed the rule. We did not edit the data just to make the check pass.

## Where it helped

Most of the time saved was the writing. The scan tables, the company list, saving each model call, and the checks for signal insights. Once I knew what those should do, asking for the code was faster than typing it.

What helped more than the typing was counting before changing anything. A lot of companies on the list had the same name. We did not start by restyling the screen. We counted. On the priority list, about 240 companies shared a display name, and 39 of them had one pediatric clinic's page title, all from a single scan. That is where the rule came from: use a page title or a certificate only when it belongs to that domain.

## Where it cost more

The agent did what I asked, including when the ask was too short.

"Use the page title as the company name" is fine when the page is that company's page. It is wrong when one address lists many unrelated domains, because the title gets copied onto all of them. That is the pediatric clinic case. The first cleanup was also incomplete. A title like "Welcome to nginx!" did not look like a junk name until after "Welcome to " was stripped, so a few names were still "nginx!" and we had to run it again. The useful fix was the narrower rule, then clear the bad names and fill them again. Another exception on top would have missed the next title.

The other case was the pipeline. Earlier we added tables, checks, and a saved-list screen because each one sounded useful at the time. Later, nothing in the app read several of them. The saved list stored a filter and did not bring those companies back. Taking that out took longer than adding it. I would put the limit in the request now: if the current app does not read it, do not add it.

## One weakness

The name rule does not apply to the findings yet. A scan on a shared address can still attach another site's exposures to every domain on that address. The company name can be right, and the score can still be treating someone else's findings as this company's. That is what I would flag before someone trusts the order of the list.
