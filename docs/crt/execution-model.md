# CRT-3C-v1.0 Execution Model

Before C3 begins, an aligned 15M FVG must already exist. During C3, price must touch or react to that FVG.

A 5M nested/refined FVG may then be used for precision, but it is optional. Execution can occur from the 15M structure when no 5M refinement is required.

Stop-loss and target placement are configuration-driven and must be tied to the selected execution structure. The implementation must not use future candles to decide whether a historical setup existed.

Entries after C3 ends are invalid for this model.
