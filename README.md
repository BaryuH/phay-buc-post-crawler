# Phây-búc crawler

**A resilient data collection tool that turns any public Facebook page into a research-ready dataset of posts and their full comment threads.**

## Why this project exists

Online communities on Facebook hold a large amount of everyday writing: opinions, emotions, jokes, complaints, and heated debates. The tool is built for Vietnamese community pages, with **UIT Confessions** as its first case study, and it works on any public page the user points it at. That text is valuable for research on sentiment analysis, emotion recognition, hate-speech detection, and topic discovery.

Collecting it is harder than it looks:

- **Official access is closed to outsiders.** Independent researchers cannot use the platform's official tools to read a page they do not administer.
- **Browser-based scraping does not scale.** Scrolling a page with tens of thousands of posts exhausts memory, misses replies hidden behind "View more" buttons, and often crashes before the job is done.
- **Long jobs get interrupted.** A dropped connection at post 2,000 should not mean starting over from post 1.
- **Heavy, robotic traffic gets blocked.** A single account sending requests at a fixed rhythm is quickly slowed down or locked out.

This project is built to remove those obstacles, so a collection run can be left alone for days and still finish with a complete, clean dataset.

## What it does

Point it at any public Facebook page, whichever one you choose, and it collects:

- **Every public post**, with its text, date, and reaction and share counts.
- **The whole comment tree**: every comment and every reply beneath it, with the author and original wording.

It then stores everything in one local database that can be exported for analysis.

## What makes it dependable

| Goal | How the project delivers it |
| :--- | :--- |
| **Never start over** | Progress is saved continuously. After a crash, power cut, or lost connection, the next run continues from where it stopped, with nothing lost and nothing duplicated. |
| **Never collect twice** | Posts and comments already stored are recognized instantly and skipped, so repeated runs only add what is new. |
| **Keep the data clean** | Near-identical comments that repeat across many posts, typically spam and advertising, are flagged so they can be filtered out of training data. |
| **Stay polite to the platform** | Requests are spaced out the way a person browses, with natural pauses and longer breaks. The pace slows down by itself when the platform shows signs of strain. |
| **Survive account problems** | Work is shared across a small pool of secondary accounts. An account that is tired, rate-limited, or logged out is rested or set aside while the others carry on. |
| **Stay light** | Memory use stays small and steady, whether the dataset holds hundreds or hundreds of thousands of comments. |

## How a run works

1. **Discover.** The tool walks through the page from newest to oldest and lists the posts it has not seen before.
2. **Read.** For each listed post it gathers all comments and replies, picking up exactly where it left off if a post was only partly read.
3. **Store.** Everything is saved safely to a local database as it arrives.
4. **Export.** The finished dataset can be exported as files ready for spreadsheets, data-analysis tools, or machine-learning pipelines.

It can run a complete collection, only pick up new posts since last time, only fill in missing comments, or limit itself to a date range.

## What you get

A single dataset in which each post carries its complete comment thread, including replies, the original text, who wrote each comment, and flags for repeated spam. It is meant to be loaded directly into the tools researchers already use.

## Browse the results

A small web app lets you read what was collected without exporting anything. Run `python -m web` and open the address it prints. You can search posts and comments (accent-insensitive, so "toi" finds "tôi"), sort and filter posts, open a post to read its full comment thread, expand or collapse replies, hide flagged repeats, and jump from a comment found by search straight to its place in the post. The app only reads the local database and listens on your own computer.

## Get started

[`TUTORIAL.md`](TUTORIAL.md) walks through the whole setup: installing, trying a demo with no account, getting your login values, finding a page's ID, running a crawl, and opening the web app.

## Who it is for

- Students and researchers working on Vietnamese natural language processing.
- Teams building datasets for sentiment, emotion, hate-speech, or topic models.
- Anyone who needs a complete and repeatable archive of a public Facebook page.

## Data policy and responsible use

- **Public data only.** The tool reads only posts and comments that are publicly visible on the page. It does not access private messages or closed groups.
- **Raw results are kept.** Author identifiers, display names, and the original wording are stored exactly as collected, for the project's own research use.
- **Gentle on the platform.** The adaptive pacing exists so that collection does not disturb the normal operation of the service.
- **Use secondary accounts.** Collection should run on dedicated secondary accounts, never on a personal main account.

## Project status

The collection pipeline is complete. It has been tested end to end against a simulated version of the platform, including interruptions, rate limiting, and account failures, and it has been run live on a handful of posts from one page, with posts, comments, and replies all collected. Larger live crawls have not been tried yet. It needs real secondary accounts and up-to-date query identifiers from the operator.

## References

Research on collecting data from online social networks, on the Vietnamese datasets this project is meant to feed, and on the techniques behind its reliability features.

**Collecting data from online social networks**

1. Catanese, S. A., De Meo, P., Ferrara, E., Fiumara, G., & Provetti, A. (2011). *Crawling Facebook for Social Network Analysis Purposes.* Proceedings of the International Conference on Web Intelligence, Mining and Semantics (WIMS '11). [arXiv:1105.6307](https://arxiv.org/abs/1105.6307)
2. Gjoka, M., Kurant, M., Butts, C. T., & Markopoulou, A. (2010). *Walking in Facebook: A Case Study of Unbiased Sampling of OSNs.* IEEE INFOCOM 2010. Extended version: *A Walk in Facebook: Uniform Sampling of Users in Online Social Networks.* [arXiv:0906.0060](https://arxiv.org/abs/0906.0060)

**Vietnamese social-media datasets (the research this project supports)**

3. Ho, V. A., et al. (2019). *Emotion Recognition for Vietnamese Social Media Text* (UIT-VSMEC). 16th International Conference of the Pacific Association for Computational Linguistics (PACLING 2019). [arXiv:1911.09339](https://arxiv.org/abs/1911.09339)
4. Luu, S. T., Nguyen, K. V., & Nguyen, N. L.-T. (2021). *A Large-Scale Dataset for Hate Speech Detection on Vietnamese Social Media Texts* (UIT-ViHSD). IEA/AIE 2021, Springer, pp. 415–426. [Dataset](https://huggingface.co/datasets/uitnlp/vihsd)

**Techniques behind the reliability features**

5. Bloom, B. H. (1970). *Space/Time Trade-offs in Hash Coding with Allowable Errors.* Communications of the ACM, 13(7), 422–426. (Instant "already collected?" checks.)
6. Alsuhaibani, M., Khan, R. U., Qamar, A. M., & Alsuhibany, S. A. (2023). *Content-Based Approach for Improving Bloom Filter Efficiency.* Applied Sciences, 13(13), 7922. [Link](https://www.mdpi.com/2076-3417/13/13/7922)
7. Charikar, M. S. (2002). *Similarity Estimation Techniques from Rounding Algorithms.* Proceedings of the 34th ACM Symposium on Theory of Computing (STOC '02), 380–388. (Near-duplicate comment detection.)
8. Chiu, D.-M., & Jain, R. (1989). *Analysis of the Increase and Decrease Algorithms for Congestion Avoidance in Computer Networks.* Computer Networks and ISDN Systems, 17(1), 1–14. (Basis of the self-adjusting request pace.)
9. Brooker, M. (2015). *Exponential Backoff and Jitter.* AWS Architecture Blog. [Link](https://aws.amazon.com/blogs/architecture/exponential-backoff-and-jitter/)
