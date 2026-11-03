"""Her second door (telegram door, 2026-09-14, task 2).

Same spine as Discord: an update becomes a ``Turn``, ``dispatch_turn``
answers it, and the reply goes back out. Nothing in here imports
``telegram`` at module level — the library is imported lazily, inside
functions, so the package loads (and the suite runs) where it is absent and
the door simply stays closed with a line saying so.
"""
