1. [Documenting Architecture Decisions](https://cognitect.com/blog/2011/11/15/documenting-architecture-decisions) — Michael Nygard (10 min)
Read this first. It's the direct antidote to your problem: a format for recording context, decision, consequences, which turns choices from permanent into revisitable. Start an docs/adr/ folder today.

2. [Hexagonal Architecture](https://alistair.cockburn.us/hexagonal-architecture) — Cockburn (15 min)
The original. Skim it. You only need the shape: your app in the middle, providers plugged into the edges. This is the pattern your Part 0.3 is already reaching for — knowing the name gives you a whole literature.

3. [Architecture Patterns with Python](https://www.cosmicpython.com/book/part1) — Percival & Gregory (free online, ~2h for the parts you need)
This is the big one, and it's almost uncannily on-target: Python, repositories, ports and adapters, swappable infrastructure. Read ch. 2 Repository, ch. 4 Service Layer, ch. 13 Dependency Injection. Skip the rest for now. Chapter 2 alone will make StorageProvider obvious.

4. 12factor: [Backing Services](https://12factor.net/backing-services) + [Config](https://12factor.net/config) (10 min)
Two short pages. "Swap the attached resource without changing code" is your entire V1→V2 thesis, stated in 300 words.

Save FastAPI's dependency docs for when you actually start the backend — it's the mechanism you'll wire providers with, but it'll mean nothing until then.