"""PaLLi's ReAct agent loop. Sibling of core/turn/ (the existing pipeline).

Entry point: dispatch.dispatch_turn() — routes to either the existing
process_turn pipeline or the new runner.run_turn ReAct loop based on
the PALLI_AGENT_LOOP env var.
"""
