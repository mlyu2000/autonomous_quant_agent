"""Self-evolution subsystem for the autonomous quant MVP.

The system evolves its OWN framework ingredients (via an evolutionary
algorithm over a bounded genome), not only the strategy artifacts it
produces. See genome.py (genome + mutation), fitness.py (mechanism-agnostic
fitness), governance.py (harmlessness gate + rejected-proposal store),
and evolve.py (the EA loop).
"""
