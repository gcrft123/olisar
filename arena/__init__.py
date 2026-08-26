"""The Olisar arena: a live Discord testbed for iterating on Olisar's behaviour.

Named ``arena`` rather than ``sandbox`` because two other things already own that word
here: ``olisar/sandbox`` is the QuickJS engine that runs marketplace extensions, and
``POST /api/admin/sandbox/chat`` is the console's memory-free test chat. This package is
neither — it is the harness that runs a *second* Olisar instance against a real Discord
server populated by emulated members, scores what comes out, and iterates.

See ``arena/README.md`` for the one-time Discord setup and the CLI.
"""
