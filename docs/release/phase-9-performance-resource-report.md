# Phase 9 performance, resource, and Windows qualification

Task 6 reports measurements only when supplied by an explicit caller. Every
sample carries its operation, warm/cold classification, implementation kind,
hardware profile, and elapsed time. Percentile reporting is withheld for
sample sets smaller than 20; a median remains available for any non-empty
sample set. Fake or synthetic adapter latency is never labelled as model or
provider latency.

The target profile is Windows 11 with an RTX 4050 Laptop GPU (6 GB VRAM) and
16 GB system RAM. Phase 8 local model work is sequential: BGE-M3, Qwen, NLI,
and VLM residency must not be inferred to be simultaneous. No Task 6 helper
downloads or loads a model, contacts a provider, or performs an external
write. Optional resource values remain unavailable rather than fabricated.

Windows qualification covers UTF-8 subprocess output, CRLF/LF bytes, path
containment, symlink-safe cleanup, PowerShell/Node/npm/Docker command shape,
and browser-startup command plumbing through an injected runner. The normal
tests use deterministic fakes; any real local timing probe is a separately
named manual release operation.
