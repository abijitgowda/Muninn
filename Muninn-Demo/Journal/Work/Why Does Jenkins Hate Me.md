# Why Does Jenkins Hate Me

Build failed again. 47th time this sprint.

Turns out the test was passing locally because I had a stale `.env` with the old DB password. Jenkins, being the honest machine it is, refused to lie about it.

Lesson learned: if it works on my machine, ship my machine.

Also Dave from platform team says we're "migrating to GitHub Actions next quarter." He said the same thing last year. And the year before.
