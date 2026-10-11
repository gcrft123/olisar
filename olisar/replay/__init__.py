"""The memory replay: a fullscreen walk through a server's history, narrated by Olisar.

An admin starts one from the console; the bot builds it in the background and the console
plays the finished report. Building it is a run (``olisar.db.models.ReplayRun``) that goes
step by step and keeps what each step found, so it survives the console closing, the bot
restarting, and the day's Gemini allowance running out partway:

1. ``gather`` and ``count`` (stats.py): code alone. When the server started, its first
   messages and first members, who arrived when, its busiest day, and Olisar's own arrival
   and renames.
2. ``moments`` (moments.py): code picks windows worth reading (busy days, long nights, the
   day a crowd arrived) and a model reads about 200 messages around each.
3. ``pick`` (pick.py): the one thing that most belongs to this server, chosen the way
   design/memory-replay/PICK.md lays out.
4. ``write`` (writing.py): the few lines that need understanding, in the persona's voice.
5. ``check`` (check.py): every quote the replay will show is read once more for anything
   hurtful or sensitive, and dropped if it is.

What it reads (scope.py): only the server's indexed channels, never a DM, never a bot, and
nobody who opted out of search or memory. People who have since left show as "Someone".
"""
